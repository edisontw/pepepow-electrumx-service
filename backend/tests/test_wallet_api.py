from fastapi import status
from fastapi.testclient import TestClient

from app.api import wallet as wallet_api
from app.main import app
from app.services.address_service import InvalidPepewAddressError, AddressUpstreamError
from app.services.tx_service import InvalidTxidError, TxNotFoundError, TxLookupError

KNOWN_ADDRESS = "PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb"
KNOWN_TXID = "a" * 64


def test_wallet_address_lookup_success(monkeypatch):
    async def fake_get_address_summary(address):
        return {
            "ok": True,
            "address": address,
            "hash160": "hash160hex",
            "scripthash": "scripthashhex",
            "balance": {"confirmed": 100000000, "unconfirmed": 50000000},
            "history_count": 2,
            "mempool_count": 0,
        }

    async def fake_get_address_history(address, limit=50, offset=0):
        return {
            "ok": True,
            "address": address,
            "history": [{"tx_hash": KNOWN_TXID, "height": 100}],
            "mempool": [],
        }

    monkeypatch.setattr(wallet_api, "get_address_summary", fake_get_address_summary)
    monkeypatch.setattr(wallet_api, "get_address_history", fake_get_address_history)

    client = TestClient(app)
    response = client.get(f"/api/wallet/address/{KNOWN_ADDRESS}")

    assert response.status_code == 200
    data = response.json()
    assert data["address"] == KNOWN_ADDRESS
    assert data["balance"]["confirmed"] == 100000000
    assert data["balance"]["confirmed_pepew"] == "1"
    assert data["balance"]["unconfirmed_pepew"] == "0.5"
    assert len(data["history"]) == 1
    assert data["history"][0]["txid"] == KNOWN_TXID
    assert data["source"] == "electrumx"
    assert data["read_only"] is True

    # Security check: No secret fields
    secret_indicators = ["mnemonic", "seed", "private", "xprv", "wif", "key", "secret"]
    for field in secret_indicators:
        assert field not in data
        assert field not in data["balance"]


def test_wallet_address_lookup_invalid_address(monkeypatch):
    async def fake_get_address_summary(address):
        raise InvalidPepewAddressError("invalid_address", "Invalid PEPEPOW address.")

    monkeypatch.setattr(wallet_api, "get_address_summary", fake_get_address_summary)

    client = TestClient(app)
    response = client.get("/api/wallet/address/invalid_addr")

    assert response.status_code == 400
    data = response.json()
    assert data["ok"] is False
    assert data["error"]["code"] == "invalid_address"


def test_wallet_address_history_success(monkeypatch):
    async def fake_get_address_history(address, limit=50, offset=0):
        return {
            "ok": True,
            "address": address,
            "history": [{"tx_hash": KNOWN_TXID, "height": 100}],
            "mempool": [{"tx_hash": "b" * 64, "height": 0}],
        }

    monkeypatch.setattr(wallet_api, "get_address_history", fake_get_address_history)

    client = TestClient(app)
    response = client.get(f"/api/wallet/history/{KNOWN_ADDRESS}?limit=10&offset=0&verbose=false")

    assert response.status_code == 200
    data = response.json()
    assert data["address"] == KNOWN_ADDRESS
    assert len(data["history"]) == 1
    assert data["history"][0]["txid"] == KNOWN_TXID
    assert len(data["mempool"]) == 1
    assert data["mempool"][0]["txid"] == "b" * 64
    assert data["source"] == "electrumx"
    assert data["read_only"] is True
    assert data["verbose"] is False


def test_wallet_address_history_verbose_success(monkeypatch):
    async def fake_get_address_history(address, limit=50, offset=0):
        return {
            "ok": True,
            "address": address,
            "history": [{"tx_hash": KNOWN_TXID, "height": 100}],
            "mempool": [],
        }

    async def fake_get_transaction_details(txid, verbose=True):
        assert txid == KNOWN_TXID
        return {
            "ok": True,
            "txid": txid,
            "data": {
                "txid": txid,
                "confirmations": 2,
                "time": 1783230886,
                "vout": [
                    {
                        "n": 0,
                        "value": 1.25,
                        "scriptPubKey": {"addresses": [KNOWN_ADDRESS]},
                    }
                ],
                "vin": [],
            },
        }

    monkeypatch.setattr(wallet_api, "get_address_history", fake_get_address_history)
    monkeypatch.setattr(wallet_api, "get_transaction_details", fake_get_transaction_details)

    client = TestClient(app)
    response = client.get(f"/api/wallet/history/{KNOWN_ADDRESS}?limit=10&offset=0&verbose=true&detail_limit=10")

    assert response.status_code == 200
    data = response.json()
    assert data["verbose"] is True
    assert data["history"][0]["direction"] == "received"
    assert data["history"][0]["amount_atoms"] == 125000000
    assert data["history"][0]["address_delta_atoms"] == 125000000


def test_wallet_tx_lookup_success(monkeypatch):
    async def fake_get_transaction_details(txid, verbose=True):
        assert verbose is True
        return {
            "ok": True,
            "txid": txid,
            "data": {"hex": "01000000...", "confirmations": 15}
        }

    monkeypatch.setattr(wallet_api, "get_transaction_details", fake_get_transaction_details)

    client = TestClient(app)
    response = client.get(f"/api/wallet/tx/{KNOWN_TXID}")

    assert response.status_code == 200
    data = response.json()
    assert data["txid"] == KNOWN_TXID
    assert data["data"]["hex"] == "01000000..."
    assert data["data"]["confirmations"] == 15
    assert data["source"] == "electrumx"
    assert data["read_only"] is True

    # Security check: No secret fields
    secret_indicators = ["mnemonic", "seed", "private", "xprv", "wif", "key", "secret"]
    for field in secret_indicators:
        assert field not in data
        assert field not in data["data"]


def test_wallet_tx_lookup_not_found(monkeypatch):
    async def fake_get_transaction_details(txid, verbose=True):
        assert verbose is True
        raise TxNotFoundError("tx_not_found", "Transaction not found.")

    monkeypatch.setattr(wallet_api, "get_transaction_details", fake_get_transaction_details)

    client = TestClient(app)
    response = client.get(f"/api/wallet/tx/{KNOWN_TXID}")

    assert response.status_code == 404
    data = response.json()
    assert data["ok"] is False
    assert data["error"]["code"] == "tx_not_found"
