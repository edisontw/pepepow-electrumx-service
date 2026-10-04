from fastapi.testclient import TestClient

from app.api import network as network_api
from app.main import app


def test_network_endpoint(monkeypatch):
    async def fake_get_network_info():
        return {
            "ok": True,
            "status": "ok",
            "height": 4980000,
            "network_hashrate_hps": "2500000000",
            "money_supply": "81234567890",
            "price_usdt": "0.0000123",
            "market_cap_usdt": "999999.99",
            "cached": False,
        }

    monkeypatch.setattr(network_api, "get_network_info", fake_get_network_info)
    client = TestClient(app)
    response = client.get("/api/network")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["height"] == 4980000
    assert data["network_hashrate_hps"] == "2500000000"


def test_cached_partial_status_is_preserved():
    from app.services.network_service import _cached_result

    cached = {"ok": True, "status": "partial", "height": 123}
    result = _cached_result(cached)

    assert result["status"] == "partial"
    assert result["ok"] is True
    assert result["cached"] is True
