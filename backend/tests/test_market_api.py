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



def test_market_service_preserves_legacy_aggregation(monkeypatch):
    import asyncio
    from app.services import market_service

    market_service.clear_market_cache()

    class FakeSettings:
        market_fetch_timeout_seconds = 5.0
        cache_market_seconds = 120
        cache_market_stale_seconds = 900
        cmc_price_proxy_url = "https://cmc.test/price"
        nonkyc_bnb_ticker_url = "https://nonkyc.test/bnb"
        nestex_ticker_url = "https://nestex.test/ticker"

    async def fake_price():
        return {
            "status": "ok",
            "price_usdt": "0.00000077",
            "volume_24h_usd": "400",
            "last_updated": "2026-10-04T00:00:00Z",
        }

    async def fake_network():
        return {"money_supply": "67462892629.918434"}

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url):
            if url == "https://cmc.test/price":
                return FakeResponse(
                    {
                        "status": "ok",
                        "price": 0.00000077,
                        "lastUpdated": "2026-10-04T00:00:00Z",
                    }
                )
            if url == "https://nonkyc.test/bnb":
                return FakeResponse({"usd_volume_est": "83"})
            if url == "https://nestex.test/ticker":
                return FakeResponse(
                    {
                        "last_price": "0.00000078",
                        "target_volume": "20",
                    }
                )
            raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(market_service, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(market_service, "get_price_info", fake_price)
    monkeypatch.setattr(market_service, "get_network_info", fake_network)
    monkeypatch.setattr(market_service.httpx, "AsyncClient", FakeClient)

    result = asyncio.run(market_service.get_market_info())

    assert result["status"] == "ok"
    assert result["sources"]["cmc"]["price_usd"] == "0.00000077"
    assert result["sources"]["nonkyc"]["volume_24h_usd"] == "483"
    assert result["sources"]["nestex"]["volume_24h_usd"] == "20"
    assert result["total_volume_24h_usd"] == "503"
    assert result["market_cap_price_source"] == "CMC"
    assert result["market_cap_onchain_usd"] == "51946.82742503719418"

    market_service.clear_market_cache()
