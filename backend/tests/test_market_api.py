from fastapi.testclient import TestClient

from app.api import market as market_api
from app.main import app


def test_market_endpoint(monkeypatch):
    async def fake_get_market_info():
        return {
            "ok": True,
            "status": "ok",
            "symbol": "PEPEW",
            "quote": "USD",
            "sources": {
                "cmc": {"status": "ok", "price_usd": "0.00000077"},
                "nonkyc": {
                    "status": "ok",
                    "price_usd": "0.00000076",
                    "volume_24h_usd": "483",
                },
                "nestex": {
                    "status": "ok",
                    "price_usd": "0.00000078",
                    "volume_24h_usd": "20",
                },
            },
            "total_volume_24h_usd": "503",
            "money_supply": "67462892629.918434",
            "market_cap_onchain_usd": "51946.827425",
            "market_cap_price_source": "CMC",
            "failures": [],
            "cached": False,
        }

    monkeypatch.setattr(market_api, "get_market_info", fake_get_market_info)
    client = TestClient(app)
    response = client.get("/api/market")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["sources"]["cmc"]["price_usd"] == "0.00000077"
    assert data["sources"]["nonkyc"]["volume_24h_usd"] == "483"
    assert data["sources"]["nestex"]["volume_24h_usd"] == "20"
    assert data["total_volume_24h_usd"] == "503"
    assert data["market_cap_price_source"] == "CMC"
