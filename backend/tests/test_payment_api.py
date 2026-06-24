from fastapi.testclient import TestClient

from app.api import payment as payment_api
from app.main import app
from app.services.address_service import InvalidPepewAddressError

KNOWN_ADDRESS = "PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb"


def test_payment_check_endpoint_success(monkeypatch):
    async def fake_check_payment(**kwargs):
        return {
            "ok": True,
            "address": kwargs["address"],
            "requested_amount": kwargs["amount"],
            "requested_sats": 100000000,
            "amount": kwargs["amount"],
            "amount_sats": 100000000,
            "amount_pepew": kwargs["amount"],
            "pepew_decimals": 8,
            "confirmed_received": "0",
            "confirmed_received_sats": 0,
            "mempool_received": "0",
            "mempool_received_sats": 0,
            "total_received": "0",
            "total_received_sats": 0,
            "received_confirmed_sats": 0,
            "received_unconfirmed_sats": 0,
            "confirmations_required": 3,
            "status": "waiting",
            "expired": False,
            "status_explanation": "No matching payment has been seen yet.",
            "message": "No matching payment has been seen yet.",
            "explorer_address_url": f"https://explorer.pepepow.net/address/{kwargs['address']}",
        }

    monkeypatch.setattr(payment_api, "check_payment", fake_check_payment)
    client = TestClient(app)
    response = client.get(f"/api/payment/check?address={KNOWN_ADDRESS}&amount=1")

    assert response.status_code == 200
    assert response.json()["status"] == "waiting"
    data = response.json()
    assert data["amount_sats"] == 100000000
    assert data["requested_sats"] == 100000000
    assert data["confirmed_received_sats"] == 0
    assert data["mempool_received_sats"] == 0
    assert data["total_received_sats"] == 0
    assert data["explorer_address_url"] == f"https://explorer.pepepow.net/address/{KNOWN_ADDRESS}"


def test_payment_check_invalid_address_returns_standard_error(monkeypatch):
    async def fake_check_payment(**_kwargs):
        raise InvalidPepewAddressError("invalid_address", "Invalid PEPEPOW address.")

    monkeypatch.setattr(payment_api, "check_payment", fake_check_payment)
    client = TestClient(app)
    response = client.get("/api/payment/check?address=invalid&amount=1")

    assert response.status_code == 400
    assert response.json() == {
        "ok": False,
        "error": {
            "code": "invalid_address",
            "message": "Invalid PEPEPOW address.",
        },
    }


def test_payment_check_real_invalid_address_returns_standard_error():
    client = TestClient(app)
    response = client.get("/api/payment/check?address=P123&amount=1")

    assert response.status_code == 400
    assert response.json() == {
        "ok": False,
        "error": {
            "code": "invalid_address",
            "message": "Invalid PEPEPOW address.",
        },
    }


def test_payment_check_invalid_amount_returns_standard_error():
    client = TestClient(app)
    response = client.get(f"/api/payment/check?address={KNOWN_ADDRESS}&amount=0")

    assert response.status_code == 400
    assert response.json()["ok"] is False
    assert response.json()["error"]["code"] == "invalid_amount"


def test_payment_check_unexpected_error_returns_standard_error(monkeypatch):
    async def fake_check_payment(**_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(payment_api, "check_payment", fake_check_payment)
    client = TestClient(app)
    response = client.get(f"/api/payment/check?address={KNOWN_ADDRESS}&amount=1")

    assert response.status_code == 500
    assert response.json() == {
        "ok": False,
        "error": {
            "code": "internal_error",
            "message": "Request failed.",
        },
    }
