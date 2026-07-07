from fastapi.testclient import TestClient

from app.main import app
from app.services.price_service import clear_price_cache, parse_price_defensively


def test_parse_price_defensively_uses_last_price():
    parsed = parse_price_defensively({"ticker": {"last_price": "0.00001230", "volume": "123.45"}})
    assert parsed is not None
    assert parsed["price"] == "0.0000123"
    assert parsed["volume_24h"] == "123.45"


def test_parse_price_defensively_uses_bid_ask_midpoint():
    parsed = parse_price_defensively({"bid": "0.00001000", "ask": "0.00001200"})
    assert parsed is not None
    assert parsed["price"] == "0.000011"


def test_parse_price_defensively_rejects_invalid_payload():
    assert parse_price_defensively({"ticker": {"last_price": "not-a-number"}}) is None


def test_price_endpoint(monkeypatch):
    clear_price_cache()

    async def fake_get_price_info():
        return {
            "ok": True,
            "status": "ok",
            "symbol": "PEPEW",
            "quote": "USDT",
            "market": "PEPEW/USDT",
            "source": "NonKYC",
            "price": "0.0000123",
            "price_usdt": "0.0000123",
            "change_24h_percent": None,
            "volume_24h": None,
            "high_24h": None,
            "low_24h": None,
            "last_updated": "2026-07-07T00:00:00Z",
            "cached": False,
            "cache_ttl_seconds": 120,
        }

    monkeypatch.setattr("app.api.price.get_price_info", fake_get_price_info)
    client = TestClient(app)
    response = client.get("/api/price")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["source"] == "NonKYC"
    assert data["price_usdt"] == "0.0000123"
