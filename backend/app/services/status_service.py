import time
from typing import Any

from ..config import get_settings
from ..electrumx.client import ElectrumXClient
from ..electrumx.errors import (
    ElectrumXConnectionError,
    ElectrumXError,
    ElectrumXMethodError,
    ElectrumXProtocolError,
    ElectrumXTimeoutError,
)
from ..electrumx.methods import headers_subscribe, server_features, server_version

_status_cache: dict[str, Any] = {
    "expires_at": 0.0,
    "value": None,
}


def clear_status_cache() -> None:
    _status_cache["expires_at"] = 0.0
    _status_cache["value"] = None


def _with_cache_metadata(status: dict[str, Any], settings: Any) -> dict[str, Any]:
    checked_at = int(status.get("checked_at") or time.time())
    return {
        **status,
        "checked_at": checked_at,
        "cache_ttl_seconds": settings.cache_status_seconds,
        "cache_age_seconds": max(0, int(time.time()) - checked_at),
    }


async def get_status() -> dict[str, Any]:
    settings = get_settings()
    now = time.monotonic()
    cached = _status_cache.get("value")

    if cached is not None and now < float(_status_cache.get("expires_at", 0.0)):
        result = dict(cached)
        result["cache"] = dict(result.get("cache", {}))
        result["cache"]["hit"] = True
        return _with_cache_metadata(result, settings)

    started = time.perf_counter()
    client = ElectrumXClient(settings)
    checks: dict[str, str] = {}
    base: dict[str, Any] = {
        "app": settings.app_name,
        "electrumx": {
            "connected": False,
            "host": settings.electrumx_host,
            "port": settings.electrumx_port,
            "ssl": settings.electrumx_use_ssl,
        },
        "checks": checks,
        "cache": {
            "enabled": settings.cache_status_seconds > 0,
            "ttl_seconds": settings.cache_status_seconds,
            "hit": False,
        },
    }

    try:
        checks["server.version"] = "running"
        version_result = await server_version(client)
        checks["server.version"] = "ok"

        features_result = None
        try:
            checks["server.features"] = "running"
            features_result = await server_features(client)
            checks["server.features"] = "ok"
        except ElectrumXError as exc:
            checks["server.features"] = f"optional_failed:{str(exc) or exc.__class__.__name__}"

        checks["blockchain.headers.subscribe"] = "running"
        header_result = await headers_subscribe(client)
        checks["blockchain.headers.subscribe"] = "ok"

        response_time_ms = round((time.perf_counter() - started) * 1000, 2)

        server_name = None
        protocol = None
        if isinstance(version_result, (list, tuple)):
            server_name = version_result[0] if len(version_result) > 0 else None
            protocol = version_result[1] if len(version_result) > 1 else None
        else:
            server_name = version_result

        height = None
        tip_hash = None
        header_hex = None
        if isinstance(header_result, dict):
            height = header_result.get("height")
            tip_hash = header_result.get("hash")
            header_hex = header_result.get("hex")

        status = {
            **base,
            "ok": True,
            "electrumx": {
                **base["electrumx"],
                "connected": True,
                "server_version": server_name,
                "protocol": protocol,
                "features": features_result if isinstance(features_result, dict) else None,
                "height": height,
                "tip_hash": tip_hash,
                "header_hex": header_hex,
                "response_time_ms": response_time_ms,
            },
            "checked_at": int(time.time()),
        }
    except ElectrumXTimeoutError as exc:
        status = _error_status(base, str(exc) or "electrumx_timeout", started)
    except ElectrumXConnectionError as exc:
        status = _error_status(base, str(exc) or "electrumx_unavailable", started)
    except ElectrumXMethodError as exc:
        status = _error_status(base, str(exc) or "electrumx_method_error", started)
    except ElectrumXProtocolError as exc:
        status = _error_status(base, str(exc) or "electrumx_protocol_error", started)
    except ElectrumXError as exc:
        status = _error_status(base, str(exc) or "electrumx_error", started)
    finally:
        await client.close()

    if settings.cache_status_seconds > 0:
        _status_cache["value"] = status
        _status_cache["expires_at"] = time.monotonic() + settings.cache_status_seconds

    return _with_cache_metadata(status, settings)


def _error_status(base: dict[str, Any], error: str, started: float) -> dict[str, Any]:
    response_time_ms = round((time.perf_counter() - started) * 1000, 2)
    return {
        **base,
        "ok": False,
        "electrumx": {
            **base["electrumx"],
            "connected": False,
            "response_time_ms": response_time_ms,
        },
        "error": error,
        "checked_at": int(time.time()),
    }
