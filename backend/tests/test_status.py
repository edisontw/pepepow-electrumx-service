from fastapi.testclient import TestClient

from app.api import status as status_api
from app.main import app
from app.services import status_service


def _ok_status(hit=False):
    return {
        "ok": True,
        "app": "pepew-light",
        "electrumx": {
            "connected": True,
            "host": "127.0.0.1",
            "port": 50001,
            "ssl": False,
            "server_version": "ElectrumX",
            "protocol": "1.4",
            "height": 123,
            "tip_hash": None,
            "response_time_ms": 12.3,
        },
        "cache": {"enabled": True, "ttl_seconds": 10, "hit": hit},
        "checked_at": 1,
        "cache_ttl_seconds": 10,
        "cache_age_seconds": 0,
    }


def _error_status():
    return {
        "ok": False,
        "app": "pepew-light",
        "electrumx": {
            "connected": False,
            "host": "127.0.0.1",
            "port": 50001,
            "ssl": False,
            "response_time_ms": 1.2,
        },
        "cache": {"enabled": True, "ttl_seconds": 10, "hit": False},
        "error": "electrumx_unavailable",
        "checked_at": 1,
        "cache_ttl_seconds": 10,
        "cache_age_seconds": 0,
    }


def test_status_endpoint_success(monkeypatch):
    async def fake_get_status():
        return _ok_status()

    monkeypatch.setattr(status_api, "get_status", fake_get_status)
    client = TestClient(app)
    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["electrumx"]["connected"] is True
    assert data["electrumx"]["height"] == 123
    assert data["cache_ttl_seconds"] == 10
    assert data["cache_age_seconds"] == 0


def test_status_endpoint_failure_returns_200(monkeypatch):
    async def fake_get_status():
        return _error_status()

    monkeypatch.setattr(status_api, "get_status", fake_get_status)
    client = TestClient(app)
    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["electrumx"]["connected"] is False
    assert data["error"] == "electrumx_unavailable"


def test_status_service_cache(monkeypatch):
    calls = {"count": 0}
    status_service.clear_status_cache()

    class FakeSettings:
        app_name = "pepew-light"
        electrumx_host = "127.0.0.1"
        electrumx_port = 50001
        electrumx_use_ssl = False
        electrumx_timeout = 1.0
        cache_status_seconds = 10

    async def fake_server_version(client):
        calls["count"] += 1
        return ["ElectrumX", "1.4"]

    async def fake_server_features(client):
        return {"hosts": {}}

    async def fake_headers_subscribe(client):
        return {"height": 456, "hex": "00"}

    monkeypatch.setattr(status_service, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(status_service, "server_version", fake_server_version)
    monkeypatch.setattr(status_service, "server_features", fake_server_features)
    monkeypatch.setattr(status_service, "headers_subscribe", fake_headers_subscribe)

    import asyncio

    first = asyncio.run(status_service.get_status())
    second = asyncio.run(status_service.get_status())

    assert first["ok"] is True
    assert first["cache"]["hit"] is False
    assert first["cache_ttl_seconds"] == 10
    assert first["cache_age_seconds"] == 0
    assert second["cache"]["hit"] is True
    assert second["cache_ttl_seconds"] == 10
    assert second["cache_age_seconds"] >= 0
    assert calls["count"] == 1

    status_service.clear_status_cache()


def test_status_service_timeout_error(monkeypatch):
    status_service.clear_status_cache()

    class FakeSettings:
        app_name = "pepew-light"
        app_env = "testing"
        electrumx_host = "127.0.0.1"
        electrumx_port = 50001
        electrumx_use_ssl = False
        electrumx_timeout = 1.0
        cache_status_seconds = 10

    from app.electrumx.errors import ElectrumXTimeoutError

    async def fake_server_version(client):
        raise ElectrumXTimeoutError("electrumx_timeout")

    monkeypatch.setattr(status_service, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(status_service, "server_version", fake_server_version)

    import asyncio
    res = asyncio.run(status_service.get_status())

    assert res["ok"] is False
    assert res["env"] == "testing"
    assert res["electrumx"]["connected"] is False
    assert res["error"] == "electrumx_timeout"
    assert res["cache"]["ttl_seconds"] == 2

    status_service.clear_status_cache()


def test_status_service_unexpected_error(monkeypatch):
    status_service.clear_status_cache()

    class FakeSettings:
        app_name = "pepew-light"
        app_env = "testing"
        electrumx_host = "127.0.0.1"
        electrumx_port = 50001
        electrumx_use_ssl = False
        electrumx_timeout = 1.0
        cache_status_seconds = 10

    async def fake_server_version(client):
        raise ValueError("unexpected database failure")

    monkeypatch.setattr(status_service, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(status_service, "server_version", fake_server_version)

    import asyncio
    res = asyncio.run(status_service.get_status())

    assert res["ok"] is False
    assert res["env"] == "testing"
    assert res["electrumx"]["connected"] is False
    assert res["error"] == "internal_error"
    assert res["cache"]["ttl_seconds"] == 2

    status_service.clear_status_cache()
