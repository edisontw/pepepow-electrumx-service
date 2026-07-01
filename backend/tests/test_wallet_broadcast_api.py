from fastapi.testclient import TestClient

from app.api import wallet as wallet_api
from app.main import app
from app.services.tx_service import InvalidRawTxError, TxUpstreamError

KNOWN_ADDRESS = "PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb"
KNOWN_TXID = "a" * 64
VALID_RAW_TX = "0100000001abcdef0123"
INVALID_RAW_TX = "not_hex_not_hex_1234"


def test_wallet_utxo_success(monkeypatch):
    async def fake_get_address_utxos(address):
        return {
            "ok": True,
            "address": address,
            "utxos": [{"txid": KNOWN_TXID, "vout": 0, "height": 100, "value": 100000000}],
            "utxo_count": 1,
            "total": 100000000,
        }

    monkeypatch.setattr(wallet_api, "get_address_utxos", fake_get_address_utxos)

    client = TestClient(app)
    response = client.get(f"/api/wallet/utxo/{KNOWN_ADDRESS}")

    assert response.status_code == 200
    data = response.json()
    assert data["address"] == KNOWN_ADDRESS
    assert data["utxos"][0]["txid"] == KNOWN_TXID
    assert data["utxos"][0]["value"] == 100000000
    assert data["source"] == "electrumx"
    assert data["read_only"] is True


def test_wallet_broadcast_success(monkeypatch):
    async def fake_broadcast_signed_raw_tx(raw_tx):
        assert raw_tx == VALID_RAW_TX
        return {"ok": True, "txid": KNOWN_TXID, "source": "electrumx"}

    monkeypatch.setattr(wallet_api, "broadcast_signed_raw_tx", fake_broadcast_signed_raw_tx)

    client = TestClient(app)
    response = client.post("/api/wallet/broadcast", json={"raw_tx": VALID_RAW_TX})

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["txid"] == KNOWN_TXID
    assert data["signed_raw_tx_only"] is True


def test_wallet_broadcast_invalid_hex(monkeypatch):
    async def fake_broadcast_signed_raw_tx(raw_tx):
        raise InvalidRawTxError("invalid_raw_tx", "Signed transaction must be hex.")

    monkeypatch.setattr(wallet_api, "broadcast_signed_raw_tx", fake_broadcast_signed_raw_tx)

    client = TestClient(app)
    response = client.post("/api/wallet/broadcast", json={"raw_tx": INVALID_RAW_TX})

    assert response.status_code == 400
    data = response.json()
    assert data["ok"] is False
    assert data["error"]["code"] == "invalid_raw_tx"


def test_wallet_broadcast_rejected(monkeypatch):
    async def fake_broadcast_signed_raw_tx(raw_tx):
        raise TxUpstreamError("broadcast_rejected", {"message": "rejected"})

    monkeypatch.setattr(wallet_api, "broadcast_signed_raw_tx", fake_broadcast_signed_raw_tx)

    client = TestClient(app)
    response = client.post("/api/wallet/broadcast", json={"raw_tx": VALID_RAW_TX})

    assert response.status_code == 503
    data = response.json()
    assert data["ok"] is False
    assert data["error"]["code"] == "broadcast_rejected"
