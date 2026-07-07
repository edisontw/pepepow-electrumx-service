import logging
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from ..config import get_settings

logger = logging.getLogger(__name__)

_PRICE_CACHE: dict[str, Any] = {
    "value": None,
    "expires_at": 0.0,
    "stale_until": 0.0,
}

_PRICE_FIELDS = ("last_price", "last", "price", "close")
_BID_FIELDS = ("bid", "buy", "highest_bid")
_ASK_FIELDS = ("ask", "sell", "lowest_ask")


def clear_price_cache() -> None:
    _PRICE_CACHE["value"] = None
    _PRICE_CACHE["expires_at"] = 0.0
    _PRICE_CACHE["stale_until"] = 0.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _decimal_to_string(value: Decimal) -> str:
    normalized = value.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _parse_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not parsed.is_finite() or parsed < 0:
        return None
    return parsed


def _unwrap_payload(data: Any) -> dict[str, Any] | None:
    if isinstance(data, list):
        if not data:
            return None
        data = data[0]
    if not isinstance(data, dict):
        return None

    for key in ("ticker", "data", "result"):
        nested = data.get(key)
        if isinstance(nested, dict):
            return nested
        if isinstance(nested, list) and nested and isinstance(nested[0], dict):
            return nested[0]
    return data


def parse_price_defensively(data: Any) -> dict[str, str | None] | None:
    payload = _unwrap_payload(data)
    if payload is None:
        return None

    price: Decimal | None = None
    for key in _PRICE_FIELDS:
        price = _parse_decimal(payload.get(key))
        if price is not None:
            break

    if price is None:
        bid = next((_parse_decimal(payload.get(key)) for key in _BID_FIELDS if _parse_decimal(payload.get(key)) is not None), None)
        ask = next((_parse_decimal(payload.get(key)) for key in _ASK_FIELDS if _parse_decimal(payload.get(key)) is not None), None)
        if bid is not None and ask is not None:
            price = (bid + ask) / Decimal("2")

    if price is None:
        return None

    return {
        "price": _decimal_to_string(price),
        "change_24h_percent": _optional_string(payload, "change_24h_percent", "change", "percent_change", "percent_change_24h"),
        "volume_24h": _optional_string(payload, "volume_24h", "volume", "base_volume"),
        "high_24h": _optional_string(payload, "high_24h", "high"),
        "low_24h": _optional_string(payload, "low_24h", "low"),
    }


def _optional_string(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = _parse_decimal(payload.get(key))
        if value is not None:
            return _decimal_to_string(value)
    return None


def _price_payload(parsed: dict[str, str | None], *, status: str, cached: bool, cache_ttl_seconds: int) -> dict[str, Any]:
    price = parsed["price"]
    return {
        "ok": status in {"ok", "stale"},
        "status": status,
        "symbol": "PEPEW",
        "quote": "USDT",
        "market": "PEPEW/USDT",
        "source": "NonKYC",
        "price": price,
        "price_usdt": price,
        "change_24h_percent": parsed.get("change_24h_percent"),
        "volume_24h": parsed.get("volume_24h"),
        "high_24h": parsed.get("high_24h"),
        "low_24h": parsed.get("low_24h"),
        "last_updated": _now_iso(),
        "cached": cached,
        "cache_ttl_seconds": cache_ttl_seconds,
    }


async def get_price_info() -> dict[str, Any]:
    settings = get_settings()
    now = time.monotonic()
    cached = _PRICE_CACHE.get("value")

    if cached is not None and now < float(_PRICE_CACHE.get("expires_at", 0.0)):
        result = dict(cached)
        result["cached"] = True
        return result

    try:
        headers = {"User-Agent": "pepew-light/0.1.0"}
        async with httpx.AsyncClient(timeout=settings.price_fetch_timeout_seconds, headers=headers) as client:
            response = await client.get(settings.nonkyc_ticker_url)
            response.raise_for_status()
            data = response.json()
        parsed = parse_price_defensively(data)
        if parsed is None:
            raise ValueError("price payload did not contain a usable price")

        result = _price_payload(
            parsed,
            status="ok",
            cached=False,
            cache_ttl_seconds=settings.cache_price_seconds,
        )
        _PRICE_CACHE["value"] = result
        _PRICE_CACHE["expires_at"] = now + settings.cache_price_seconds
        _PRICE_CACHE["stale_until"] = now + settings.cache_price_stale_seconds
        return result
    except Exception as exc:
        logger.warning("NonKYC price fetch failed: %s", exc)

    if cached is not None and now < float(_PRICE_CACHE.get("stale_until", 0.0)):
        result = dict(cached)
        result["status"] = "stale"
        result["cached"] = True
        result["message"] = "Price data is temporarily stale because the upstream source is unavailable."
        return result

    return {
        "ok": False,
        "status": "unavailable",
        "symbol": "PEPEW",
        "quote": "USDT",
        "market": "PEPEW/USDT",
        "source": "NonKYC",
        "price": None,
        "price_usdt": None,
        "change_24h_percent": None,
        "volume_24h": None,
        "high_24h": None,
        "low_24h": None,
        "last_updated": None,
        "cached": False,
        "cache_ttl_seconds": settings.cache_price_seconds,
        "message": "Price data is temporarily unavailable.",
    }
