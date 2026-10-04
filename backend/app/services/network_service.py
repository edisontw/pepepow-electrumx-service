import asyncio
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from ..config import get_settings
from .price_service import get_price_info
from .status_service import get_status

logger = logging.getLogger(__name__)

_NETWORK_CACHE: dict[str, Any] = {
    "value": None,
    "expires_at": 0.0,
    "stale_until": 0.0,
}


def clear_network_cache() -> None:
    _NETWORK_CACHE["value"] = None
    _NETWORK_CACHE["expires_at"] = 0.0
    _NETWORK_CACHE["stale_until"] = 0.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _decimal(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not result.is_finite() or result < 0:
        return None
    return result


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


async def _fetch_text(client: httpx.AsyncClient, url: str) -> str:
    response = await client.get(url)
    response.raise_for_status()
    return response.text.strip()


def _cached_result(cached: dict[str, Any], *, status: str | None = None) -> dict[str, Any]:
    result = dict(cached)
    effective_status = status or str(result.get("status") or "ok")
    result["status"] = effective_status
    result["ok"] = effective_status in {"ok", "partial", "stale"}
    result["cached"] = True
    if effective_status == "stale":
        result["message"] = (
            "Network aggregate is temporarily stale because an upstream source "
            "is unavailable."
        )
    return result


async def get_network_info() -> dict[str, Any]:
    settings = get_settings()
    now = time.monotonic()
    cached = _NETWORK_CACHE.get("value")

    if cached is not None and now < float(_NETWORK_CACHE.get("expires_at", 0.0)):
        return _cached_result(cached)

    height = None
    hashrate = None
    supply = None
    price = None
    failures: list[str] = []

    try:
        status_payload = await get_status()
        height = status_payload.get("electrumx", {}).get("height")
        if height is not None:
            height = int(height)
    except Exception as exc:
        logger.warning("Light status aggregation failed: %s", exc)
        failures.append("height")

    try:
        price_payload = await get_price_info()
        if price_payload.get("status") in {"ok", "stale"}:
            price = _decimal(
                price_payload.get("price_usdt") or price_payload.get("price")
            )
    except Exception as exc:
        logger.warning("Light price aggregation failed: %s", exc)
        failures.append("price")

    explorer_base = settings.pepew_explorer_base_url.rstrip("/")
    headers = {"User-Agent": "pepew-light/0.1.0"}
    try:
        async with httpx.AsyncClient(
            timeout=settings.network_fetch_timeout_seconds,
            headers=headers,
        ) as client:
            hash_result, supply_result = await asyncio.gather(
                _fetch_text(client, f"{explorer_base}/api/getnetworkhashps"),
                _fetch_text(client, f"{explorer_base}/ext/getmoneysupply"),
                return_exceptions=True,
            )

        if isinstance(hash_result, Exception):
            failures.append("hashrate")
            logger.warning("Explorer hashrate fetch failed: %s", hash_result)
        else:
            hashrate = _decimal(hash_result)
            if hashrate is None:
                failures.append("hashrate")

        if isinstance(supply_result, Exception):
            failures.append("supply")
            logger.warning("Explorer money supply fetch failed: %s", supply_result)
        else:
            supply = _decimal(supply_result)
            if supply is None:
                failures.append("supply")
    except Exception as exc:
        logger.warning("Explorer aggregate fetch failed: %s", exc)
        failures.extend(
            name for name in ("hashrate", "supply") if name not in failures
        )

    market_cap = supply * price if supply is not None and price is not None else None
    available = sum(
        value is not None for value in (height, hashrate, supply, price)
    )

    if available == 0:
        if cached is not None and now < float(
            _NETWORK_CACHE.get("stale_until", 0.0)
        ):
            return _cached_result(cached, status="stale")
        return {
            "ok": False,
            "status": "unavailable",
            "height": None,
            "network_hashrate_hps": None,
            "money_supply": None,
            "price_usdt": None,
            "market_cap_usdt": None,
            "sources": {
                "height": "ElectrumX via PEPEW Light",
                "hashrate": "PEPEPOW Explorer via PEPEW Light",
                "supply": "PEPEPOW Explorer via PEPEW Light",
                "price": "NonKYC via PEPEW Light",
            },
            "failures": sorted(set(failures)),
            "last_updated": None,
            "cached": False,
            "cache_ttl_seconds": settings.cache_network_seconds,
        }

    result = {
        "ok": True,
        "status": "ok" if not failures else "partial",
        "height": height,
        "network_hashrate_hps": _decimal_text(hashrate),
        "money_supply": _decimal_text(supply),
        "price_usdt": _decimal_text(price),
        "market_cap_usdt": _decimal_text(market_cap),
        "sources": {
            "height": "ElectrumX via PEPEW Light",
            "hashrate": "PEPEPOW Explorer via PEPEW Light",
            "supply": "PEPEPOW Explorer via PEPEW Light",
            "price": "NonKYC via PEPEW Light",
        },
        "failures": sorted(set(failures)),
        "last_updated": _now_iso(),
        "cached": False,
        "cache_ttl_seconds": settings.cache_network_seconds,
    }
    _NETWORK_CACHE["value"] = result
    _NETWORK_CACHE["expires_at"] = now + settings.cache_network_seconds
    _NETWORK_CACHE["stale_until"] = now + settings.cache_network_stale_seconds
    return result
