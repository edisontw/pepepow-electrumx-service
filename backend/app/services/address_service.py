import time
from typing import Any

from ..config import get_settings
from ..electrumx.client import ElectrumXClient
from ..electrumx.errors import ElectrumXError
from ..electrumx.methods import (
    scripthash_get_balance,
    scripthash_get_history,
    scripthash_get_mempool,
    scripthash_list_unspent,
    server_version,
)
from ..pepepow.address import (
    InvalidAddressError,
    InvalidAddressVersionError,
    InvalidChecksumError,
    PepepowAddressError,
    decode_pepew_p2pkh_address,
)
from ..pepepow.scripthash import address_to_electrumx_scripthash
from .cache_service import TTLCache

_address_cache = TTLCache()
_history_cache = TTLCache()
_utxo_cache = TTLCache()


def clear_address_caches() -> None:
    """Clear address-related in-memory caches for tests and maintenance."""
    _address_cache.clear()
    _history_cache.clear()
    _utxo_cache.clear()


class AddressLookupError(Exception):
    code = "address_lookup_error"


class InvalidPepewAddressError(AddressLookupError):
    code = "invalid_address"

    def __init__(self, code: str = "invalid_address", message: str = "Invalid PEPEPOW address.") -> None:
        super().__init__(code)
        self.code = code
        self.message = message


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


def _normalize_utxos(utxos: Any) -> list[dict[str, Any]]:
    if not isinstance(utxos, list):
        return []

    normalized: list[dict[str, Any]] = []
    for item in utxos:
        if not isinstance(item, dict):
            continue
        tx_hash = item.get("tx_hash")
        tx_pos = item.get("tx_pos")
        value = item.get("value")
        height = item.get("height")
        if not isinstance(tx_hash, str):
            continue
        try:
            vout = int(tx_pos)
            sats = int(value)
            block_height = int(height or 0)
        except (TypeError, ValueError):
            continue
        if vout < 0 or sats <= 0:
            continue
        normalized.append({
            "txid": tx_hash,
            "vout": vout,
            "height": block_height,
            "value": sats,
        })
    return normalized


def _history_sort_key(item: dict[str, Any]) -> tuple[int, int]:
    height = int(item.get("height") or 0)
    is_unconfirmed = 1 if height <= 0 else 0
    return (is_unconfirmed, height)


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


def _paginate(items: list[dict[str, Any]], limit: int, offset: int) -> tuple[list[dict[str, Any]], bool]:
    start = max(offset, 0)
    end = start + max(limit, 0)
    return items[start:end], end < len(items)


def _safe_address_parts(address: str) -> tuple[str, str, str]:
    try:
        value = address.strip()
        if not value:
            raise InvalidPepewAddressError("empty_address", "Please enter a PEPEPOW address.")
        if len(value) > 128:
            raise InvalidPepewAddressError("invalid_address", "Invalid PEPEPOW address.")
        _version, hash160 = decode_pepew_p2pkh_address(value)
        scripthash = address_to_electrumx_scripthash(value)
    except InvalidPepewAddressError:
        raise
    except AttributeError as exc:
        raise InvalidPepewAddressError("empty_address", "Please enter a PEPEPOW address.") from exc
    except InvalidChecksumError as exc:
        raise InvalidPepewAddressError("invalid_address_checksum", "Address checksum is invalid.") from exc
    except InvalidAddressVersionError as exc:
        raise InvalidPepewAddressError("unsupported_address_prefix", "Address prefix is not supported.") from exc
    except InvalidAddressError as exc:
        message = str(exc).lower()
        if "prefix" in message:
            raise InvalidPepewAddressError("unsupported_address_prefix", "Address prefix is not supported.") from exc
        if "empty" in message:
            raise InvalidPepewAddressError("empty_address", "Please enter a PEPEPOW address.") from exc
        raise InvalidPepewAddressError("invalid_address", "Invalid PEPEPOW address.") from exc
    except PepepowAddressError as exc:
        raise InvalidPepewAddressError("invalid_address", "Invalid PEPEPOW address.") from exc
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


async def _identify_client(client: ElectrumXClient) -> None:
    # Some ElectrumX servers require server.version before any blockchain.* method on each TCP session.
    await server_version(client)


async def get_address_summary(address: str, *, fresh: bool = False) -> dict[str, Any]:
    settings = get_settings()
    normalized_address, hash160, scripthash = _safe_address_parts(address)
    cache_key = f"summary:{scripthash}"

    cached = None if fresh else _address_cache.get(cache_key)
    if cached is not None:
        result = dict(cached)
        result["cache"] = dict(result.get("cache", {}))
        result["cache"]["hit"] = True
        return result

    client = ElectrumXClient(settings)
    started = time.perf_counter()
    try:
        await _identify_client(client)
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
            "bypass": fresh,
        },
    }

    if settings.cache_balance_seconds > 0 and not fresh:
        _address_cache.set(cache_key, result, settings.cache_balance_seconds)
    return result


async def get_address_utxos(address: str, *, fresh: bool = False) -> dict[str, Any]:
    settings = get_settings()
    normalized_address, hash160, scripthash = _safe_address_parts(address)
    cache_key = f"utxo:{scripthash}"

    cached = None if fresh else _utxo_cache.get(cache_key)
    if cached is not None:
        result = dict(cached)
        result["cache"] = dict(result.get("cache", {}))
        result["cache"]["hit"] = True
        return result

    client = ElectrumXClient(settings)
    started = time.perf_counter()
    try:
        await _identify_client(client)
        utxo_result = await scripthash_list_unspent(client, scripthash)
    except ElectrumXError as exc:
        raise AddressUpstreamError(_electrumx_error_code(exc), _safe_electrumx_error_detail(exc)) from exc
    finally:
        await client.close()

    utxos = _normalize_utxos(utxo_result)
    result = {
        "ok": True,
        "address": normalized_address,
        "hash160": hash160,
        "scripthash": scripthash,
        "utxos": utxos,
        "utxo_count": len(utxos),
        "total": sum(int(item["value"]) for item in utxos),
        "response_time_ms": round((time.perf_counter() - started) * 1000, 2),
        "checked_at": int(time.time()),
        "cache": {
            "enabled": settings.cache_balance_seconds > 0,
            "ttl_seconds": min(settings.cache_balance_seconds, 20),
            "hit": False,
            "bypass": fresh,
        },
    }

    # UTXOs are spend-sensitive for wallet sends. Cache only normal page reads;
    # wallet send flows should pass fresh=1 to avoid reusing recently spent outpoints.
    if settings.cache_balance_seconds > 0 and not fresh:
        _utxo_cache.set(cache_key, result, min(settings.cache_balance_seconds, 20))
    return result


async def get_address_history(address: str, limit: int = 50, offset: int = 0, *, fresh: bool = False) -> dict[str, Any]:
    settings = get_settings()
    normalized_address, hash160, scripthash = _safe_address_parts(address)
    cache_key = f"history:raw:{scripthash}"
    limit = max(1, min(limit, 500))
    offset = max(offset, 0)

    cached = None if fresh else _history_cache.get(cache_key)
    if cached is not None:
        result = dict(cached)
        full_history = list(result.get("history", []))
        history_page, has_more = _paginate(full_history, limit, offset)
        result["history"] = history_page
        result["limit"] = limit
        result["offset"] = offset
        result["has_more"] = has_more
        result["cache"] = dict(result.get("cache", {}))
        result["cache"]["hit"] = True
        return result

    client = ElectrumXClient(settings)
    started = time.perf_counter()
    try:
        await _identify_client(client)
        history_result = await scripthash_get_history(client, scripthash)
        mempool_result = await scripthash_get_mempool(client, scripthash)
    except ElectrumXError as exc:
        raise AddressUpstreamError(_electrumx_error_code(exc), _safe_electrumx_error_detail(exc)) from exc
    finally:
        await client.close()

    history = sorted(_normalize_history(history_result), key=_history_sort_key, reverse=True)
    mempool = _normalize_mempool(mempool_result)
    history_page, has_more = _paginate(history, limit, offset)
    result = {
        "ok": True,
        "address": normalized_address,
        "hash160": hash160,
        "scripthash": scripthash,
        "history": history_page,
        "mempool": mempool,
        "history_count": len(history),
        "mempool_count": len(mempool),
        "limit": limit,
        "offset": offset,
        "has_more": has_more,
        "response_time_ms": round((time.perf_counter() - started) * 1000, 2),
        "checked_at": int(time.time()),
        "cache": {
            "enabled": settings.cache_history_seconds > 0,
            "ttl_seconds": settings.cache_history_seconds,
            "hit": False,
            "bypass": fresh,
        },
    }

    if settings.cache_history_seconds > 0 and not fresh:
        cached_result = dict(result)
        cached_result["history"] = history
        _history_cache.set(cache_key, cached_result, settings.cache_history_seconds)
    return result
