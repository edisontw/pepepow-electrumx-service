import time
from typing import Any

from ..config import get_settings
from ..electrumx.client import ElectrumXClient
from ..electrumx.errors import ElectrumXError
from ..electrumx.methods import (
    scripthash_get_balance,
    scripthash_get_history,
    scripthash_get_mempool,
)
from ..pepepow.address import PepepowAddressError, decode_pepew_p2pkh_address
from ..pepepow.scripthash import address_to_electrumx_scripthash
from .cache_service import TTLCache

_address_cache = TTLCache()
_history_cache = TTLCache()


class AddressLookupError(Exception):
    code = "address_lookup_error"


class InvalidPepewAddressError(AddressLookupError):
    code = "invalid_address"


class AddressUpstreamError(AddressLookupError):
    code = "electrumx_error"

    def __init__(self, code: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail or {}


def _normalize_balance(balance: Any) -> dict[str, int]:
    if not isinstance(balance, dict):
        return {"confirmed": 0, "unconfirmed": 0}

    return {
        "confirmed": int(balance.get("confirmed") or 0),
        "unconfirmed": int(balance.get("unconfirmed") or 0),
    }


def _normalize_history(history: Any) -> list[dict[str, Any]]:
    if not isinstance(history, list):
        return []

    normalized = []
    for item in history:
        if not isinstance(item, dict):
            continue
        tx_hash = item.get("tx_hash")
        height = item.get("height")
        if not isinstance(tx_hash, str):
            continue
        normalized.append({"tx_hash": tx_hash, "height": int(height or 0)})
    return normalized


def _normalize_mempool(mempool: Any) -> list[dict[str, Any]]:
    if not isinstance(mempool, list):
        return []

    normalized = []
    for item in mempool:
        if not isinstance(item, dict):
            continue
        tx_hash = item.get("tx_hash")
        height = item.get("height")
        fee = item.get("fee")
        if not isinstance(tx_hash, str):
            continue
        row: dict[str, Any] = {"tx_hash": tx_hash, "height": int(height or 0)}
        if fee is not None:
            row["fee"] = int(fee)
        normalized.append(row)
    return normalized


def _safe_address_parts(address: str) -> tuple[str, str, str]:
    try:
        value = address.strip()
        _version, hash160 = decode_pepew_p2pkh_address(value)
        scripthash = address_to_electrumx_scripthash(value)
    except (AttributeError, PepepowAddressError) as exc:
        raise InvalidPepewAddressError("invalid_address") from exc
    return value, hash160.hex(), scripthash


def _electrumx_error_code(exc: ElectrumXError) -> str:
    return getattr(exc, "code", None) or str(exc) or "electrumx_error"


def _safe_electrumx_error_detail(exc: ElectrumXError) -> dict[str, Any]:
    data = getattr(exc, "data", None)
    if not isinstance(data, dict):
        return {}

    detail: dict[str, Any] = {}
    code = data.get("code")
    message = data.get("message")
    if isinstance(code, int):
        detail["upstream_code"] = code
    if isinstance(message, str):
        # Keep only the upstream method message. Do not include paths, config, or request payloads.
        detail["upstream_message"] = message[:200]
    return detail


async def get_address_summary(address: str) -> dict[str, Any]:
    settings = get_settings()
    normalized_address, hash160, scripthash = _safe_address_parts(address)
    cache_key = f"summary:{scripthash}"

    cached = _address_cache.get(cache_key)
    if cached is not None:
        result = dict(cached)
        result["cache"] = dict(result.get("cache", {}))
        result["cache"]["hit"] = True
        return result

    client = ElectrumXClient(settings)
    started = time.perf_counter()
    try:
        balance_result = await scripthash_get_balance(client, scripthash)
        history_result = await scripthash_get_history(client, scripthash)
        mempool_result = await scripthash_get_mempool(client, scripthash)
    except ElectrumXError as exc:
        raise AddressUpstreamError(_electrumx_error_code(exc), _safe_electrumx_error_detail(exc)) from exc
    finally:
        await client.close()

    balance = _normalize_balance(balance_result)
    history = _normalize_history(history_result)
    mempool = _normalize_mempool(mempool_result)
    result = {
        "ok": True,
        "address": normalized_address,
        "hash160": hash160,
        "scripthash": scripthash,
        "balance": balance,
        "history_count": len(history),
        "mempool_count": len(mempool),
        "response_time_ms": round((time.perf_counter() - started) * 1000, 2),
        "checked_at": int(time.time()),
        "cache": {
            "enabled": settings.cache_balance_seconds > 0,
            "ttl_seconds": settings.cache_balance_seconds,
            "hit": False,
        },
    }

    if settings.cache_balance_seconds > 0:
        _address_cache.set(cache_key, result, settings.cache_balance_seconds)
    return result


async def get_address_history(address: str) -> dict[str, Any]:
    settings = get_settings()
    normalized_address, hash160, scripthash = _safe_address_parts(address)
    cache_key = f"history:{scripthash}"

    cached = _history_cache.get(cache_key)
    if cached is not None:
        result = dict(cached)
        result["cache"] = dict(result.get("cache", {}))
        result["cache"]["hit"] = True
        return result

    client = ElectrumXClient(settings)
    started = time.perf_counter()
    try:
        history_result = await scripthash_get_history(client, scripthash)
        mempool_result = await scripthash_get_mempool(client, scripthash)
    except ElectrumXError as exc:
        raise AddressUpstreamError(_electrumx_error_code(exc), _safe_electrumx_error_detail(exc)) from exc
    finally:
        await client.close()

    history = _normalize_history(history_result)
    mempool = _normalize_mempool(mempool_result)
    result = {
        "ok": True,
        "address": normalized_address,
        "hash160": hash160,
        "scripthash": scripthash,
        "history": history,
        "mempool": mempool,
        "history_count": len(history),
        "mempool_count": len(mempool),
        "response_time_ms": round((time.perf_counter() - started) * 1000, 2),
        "checked_at": int(time.time()),
        "cache": {
            "enabled": settings.cache_history_seconds > 0,
            "ttl_seconds": settings.cache_history_seconds,
            "hit": False,
        },
    }

    if settings.cache_history_seconds > 0:
        _history_cache.set(cache_key, result, settings.cache_history_seconds)
    return result


def clear_address_caches() -> None:
    global _address_cache, _history_cache
    _address_cache = TTLCache()
    _history_cache = TTLCache()
