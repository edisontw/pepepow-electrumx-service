import asyncio
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from ..config import get_settings
from .network_service import get_network_info
from .price_service import get_price_info

logger = logging.getLogger(__name__)

_MARKET_CACHE: dict[str, Any] = {
    "value": None,
    "expires_at": 0.0,
    "stale_until": 0.0,
}


def clear_market_cache() -> None:
    _MARKET_CACHE["value"] = None
    _MARKET_CACHE["expires_at"] = 0.0
    _MARKET_CACHE["stale_until"] = 0.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not parsed.is_finite() or parsed < 0:
        return None
    return parsed


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


async def _fetch_json(client: httpx.AsyncClient, url: str) -> dict[str, Any]:
    response = await client.get(url)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("upstream returned non-object JSON")
    return payload


def _source(status: str, **fields: Any) -> dict[str, Any]:
    return {"status": status, **fields}


def _cached_result(cached: dict[str, Any], *, stale: bool = False) -> dict[str, Any]:
    result = dict(cached)
    result["cached"] = True
    if stale:
        result["status"] = "stale"
        result["ok"] = True
        result["message"] = (
            "Market aggregate is temporarily stale because upstream sources "
            "could not be refreshed."
        )
    return result


async def get_market_info() -> dict[str, Any]:
    settings = get_settings()
    now = time.monotonic()
    cached = _MARKET_CACHE.get("value")

    if cached is not None and now < float(_MARKET_CACHE.get("expires_at", 0.0)):
        return _cached_result(cached)

    nonkyc_task = asyncio.create_task(get_price_info())
    network_task = asyncio.create_task(get_network_info())

    headers = {"User-Agent": "pepew-light/0.1.0"}
    async with httpx.AsyncClient(
        timeout=settings.market_fetch_timeout_seconds,
        headers=headers,
    ) as client:
        cmc_result, bnb_result, nestex_result = await asyncio.gather(
            _fetch_json(client, settings.cmc_price_proxy_url),
            _fetch_json(client, settings.nonkyc_bnb_ticker_url),
            _fetch_json(client, settings.nestex_ticker_url),
            return_exceptions=True,
        )

    nonkyc_result, network_result = await asyncio.gather(
        nonkyc_task,
        network_task,
        return_exceptions=True,
    )

    failures: list[str] = []

    cmc_price = None
    cmc_status = "unavailable"
    cmc_updated = None
    if isinstance(cmc_result, Exception):
        failures.append("cmc")
        logger.warning("CMC proxy fetch failed: %s", cmc_result)
    else:
        cmc_price = _decimal(cmc_result.get("price"))
        upstream_status = str(cmc_result.get("status") or "unavailable")
        if cmc_price is not None and upstream_status in {"ok", "stale"}:
            cmc_status = upstream_status
            cmc_updated = cmc_result.get("lastUpdated")
            if upstream_status == "stale":
                failures.append("cmc_stale")
        else:
            failures.append("cmc")

    nonkyc_price = None
    nonkyc_usdt_volume = None
    nonkyc_status = "unavailable"
    nonkyc_updated = None
    if isinstance(nonkyc_result, Exception):
        failures.append("nonkyc_usdt")
        logger.warning("NonKYC cached price fetch failed: %s", nonkyc_result)
    else:
        nonkyc_price = _decimal(
            nonkyc_result.get("price_usdt") or nonkyc_result.get("price")
        )
        nonkyc_usdt_volume = _decimal(nonkyc_result.get("volume_24h_usd"))
        upstream_status = str(nonkyc_result.get("status") or "unavailable")
        if nonkyc_price is not None and upstream_status in {"ok", "stale"}:
            nonkyc_status = upstream_status
            nonkyc_updated = nonkyc_result.get("last_updated")
            if upstream_status == "stale":
                failures.append("nonkyc_usdt_stale")
        else:
            failures.append("nonkyc_usdt")

    nonkyc_bnb_volume = None
    if isinstance(bnb_result, Exception):
        failures.append("nonkyc_bnb")
        logger.warning("NonKYC BNB ticker fetch failed: %s", bnb_result)
    else:
        nonkyc_bnb_volume = _decimal(bnb_result.get("usd_volume_est"))
        if nonkyc_bnb_volume is None:
            failures.append("nonkyc_bnb")

    nonkyc_volume = None
    available_nonkyc_volumes = [
        value
        for value in (nonkyc_usdt_volume, nonkyc_bnb_volume)
        if value is not None
    ]
    if available_nonkyc_volumes:
        nonkyc_volume = sum(available_nonkyc_volumes, Decimal("0"))

    nestex_price = None
    nestex_volume = None
    nestex_status = "unavailable"
    if isinstance(nestex_result, Exception):
        failures.append("nestex")
        logger.warning("NestEx ticker fetch failed: %s", nestex_result)
    else:
        nestex_price = _decimal(nestex_result.get("last_price"))
        nestex_volume = _decimal(nestex_result.get("target_volume"))
        if nestex_price is not None:
            nestex_status = "ok"
        else:
            failures.append("nestex")

    supply = None
    if isinstance(network_result, Exception):
        failures.append("supply")
        logger.warning("Network aggregate fetch failed: %s", network_result)
    else:
        supply = _decimal(network_result.get("money_supply"))
        if supply is None:
            failures.append("supply")

    total_volume = None
    volumes = [
        value for value in (nonkyc_volume, nestex_volume) if value is not None
    ]
    if volumes:
        total_volume = sum(volumes, Decimal("0"))

    market_cap = (
        supply * cmc_price
        if supply is not None and cmc_price is not None
        else None
    )

    available_prices = sum(
        value is not None for value in (cmc_price, nonkyc_price, nestex_price)
    )
    if available_prices == 0:
        if cached is not None and now < float(
            _MARKET_CACHE.get("stale_until", 0.0)
        ):
            return _cached_result(cached, stale=True)
        return {
            "ok": False,
            "status": "unavailable",
            "symbol": "PEPEW",
            "quote": "USD",
            "sources": {
                "cmc": _source(cmc_status, price_usd=None),
                "nonkyc": _source(
                    nonkyc_status,
                    price_usd=None,
                    volume_24h_usd=None,
                ),
                "nestex": _source(
                    nestex_status,
                    price_usd=None,
                    volume_24h_usd=None,
                ),
            },
            "total_volume_24h_usd": None,
            "money_supply": _decimal_text(supply),
            "market_cap_onchain_usd": None,
            "market_cap_price_source": "CMC",
            "failures": sorted(set(failures)),
            "last_updated": None,
            "cached": False,
            "cache_ttl_seconds": settings.cache_market_seconds,
        }

    degraded = bool(failures) or available_prices < 3
    result = {
        "ok": True,
        "status": "partial" if degraded else "ok",
        "symbol": "PEPEW",
        "quote": "USD",
        "sources": {
            "cmc": _source(
                cmc_status,
                price_usd=_decimal_text(cmc_price),
                last_updated=cmc_updated,
                via="api.pepepow.net CMC cache",
            ),
            "nonkyc": _source(
                nonkyc_status,
                price_usd=_decimal_text(nonkyc_price),
                volume_24h_usd=_decimal_text(nonkyc_volume),
                last_updated=nonkyc_updated,
                markets={
                    "PEPEW_USDT": {
                        "volume_24h_usd": _decimal_text(nonkyc_usdt_volume),
                    },
                    "PEPEW_BNB": {
                        "volume_24h_usd": _decimal_text(nonkyc_bnb_volume),
                    },
                },
            ),
            "nestex": _source(
                nestex_status,
                price_usd=_decimal_text(nestex_price),
                volume_24h_usd=_decimal_text(nestex_volume),
                market="PEPEW/USDT",
            ),
        },
        "total_volume_24h_usd": _decimal_text(total_volume),
        "money_supply": _decimal_text(supply),
        "market_cap_onchain_usd": _decimal_text(market_cap),
        "market_cap_price_source": "CMC",
        "failures": sorted(set(failures)),
        "last_updated": _now_iso(),
        "cached": False,
        "cache_ttl_seconds": settings.cache_market_seconds,
    }

    _MARKET_CACHE["value"] = result
    _MARKET_CACHE["expires_at"] = now + settings.cache_market_seconds
    _MARKET_CACHE["stale_until"] = now + settings.cache_market_stale_seconds
    return result
