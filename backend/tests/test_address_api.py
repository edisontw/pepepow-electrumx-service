from fastapi.testclient import TestClient

from app.api import address as address_api
from app.main import app

KNOWN_ADDRESS = "PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb"
KNOWN_SCRIPTHASH = "3c5cab2c9f663ed292a0fdff3e681569c3f5d6741ca4b2ca4ea6281b9d4d4298"


def test_address_lookup_endpoint_success(monkeypatch):
    async def fake_get_address_summary(address):
        return {
            "ok": True,
            "address": address,
            "scripthash": KNOWN_SCRIPTHASH,
            "balance": {"confirmed": 123, "unconfirmed": 0},
            "history_count": 1,
            "mempool_count": 0,
        }

    monkeypatch.setattr(address_api, "get_address_summary", fake_get_address_summary)
    client = TestClient(app)
    response = client.get(f"/api/address/{KNOWN_ADDRESS}")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["address"] == KNOWN_ADDRESS
    assert data["scripthash"] == KNOWN_SCRIPTHASH
    assert data["balance"]["confirmed"] == 123
    assert data["history_count"] == 1


def test_address_history_endpoint_success(monkeypatch):
    seen = {}

    async def fake_get_address_history(address, limit=50, offset=0):
        seen["limit"] = limit
        seen["offset"] = offset
        return {
            "ok": True,
            "address": address,
            "scripthash": KNOWN_SCRIPTHASH,
            "history": [{"tx_hash": "a" * 64, "height": 123}],
            "mempool": [],
            "history_count": 1,
            "mempool_count": 0,
            "limit": limit,
            "offset": offset,
            "has_more": False,
        }

    monkeypatch.setattr(address_api, "get_address_history", fake_get_address_history)
    client = TestClient(app)
    response = client.get(f"/api/address/{KNOWN_ADDRESS}/history?limit=25&offset=10")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["history"][0]["tx_hash"] == "a" * 64
    assert data["history_count"] == 1
    assert data["limit"] == 25
    assert data["offset"] == 10
    assert seen == {"limit": 25, "offset": 10}


def test_address_history_invalid_limit_returns_422():
    client = TestClient(app)
    response = client.get(f"/api/address/{KNOWN_ADDRESS}/history?limit=501")

    assert response.status_code == 422


def test_address_lookup_invalid_address_returns_400():
    client = TestClient(app)
    response = client.get("/api/address/P123")

    assert response.status_code == 400
    assert response.json()["detail"] == {"ok": False, "error": "invalid_address"}
