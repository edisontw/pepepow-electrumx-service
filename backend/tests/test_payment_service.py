import asyncio
import time

import pytest

from app.services import payment_service
from app.services.payment_service import InvalidPaymentAmountError, check_payment, parse_pepew_amount

KNOWN_ADDRESS = "PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb"


class FakeElectrumXClient:
    def __init__(self, settings):
        self.settings = settings

    async def close(self):
        return None


def install_payment_fakes(monkeypatch, balance, history=None, mempool=None):
    async def fake_identify(_client):
        return None

    async def fake_balance(_client, _scripthash):
        return balance

    async def fake_history(_client, _scripthash):
        return history if history is not None else []

    async def fake_mempool(_client, _scripthash):
        return mempool if mempool is not None else []

    monkeypatch.setattr(payment_service, "ElectrumXClient", FakeElectrumXClient)
    monkeypatch.setattr(payment_service, "_identify_client", fake_identify)
    monkeypatch.setattr(payment_service, "scripthash_get_balance", fake_balance)
    monkeypatch.setattr(payment_service, "scripthash_get_history", fake_history)
    monkeypatch.setattr(payment_service, "scripthash_get_mempool", fake_mempool)


def test_parse_pepew_amount_uses_eight_decimals():
    assert parse_pepew_amount("1", decimals=8) == 100000000
    assert parse_pepew_amount("1000", decimals=8) == 100000000000
    assert parse_pepew_amount("0.00000001", decimals=8) == 1


@pytest.mark.parametrize("amount", ["0", "-1", "abc", "1.000000001"])
def test_parse_pepew_amount_rejects_invalid_values(amount):
    with pytest.raises(InvalidPaymentAmountError):
        parse_pepew_amount(amount, decimals=8)


def test_check_payment_waiting(monkeypatch):
    install_payment_fakes(monkeypatch, {"confirmed": 0, "unconfirmed": 0})

    result = asyncio.run(check_payment(KNOWN_ADDRESS, "1"))

    assert result["status"] == "waiting"
    assert result["received_confirmed_sats"] == 0
    assert result["received_unconfirmed_sats"] == 0


def test_check_payment_seen_in_mempool(monkeypatch):
    install_payment_fakes(
        monkeypatch,
        {"confirmed": 0, "unconfirmed": 0},
        mempool=[{"tx_hash": "a" * 64, "height": 0}],
    )

    result = asyncio.run(check_payment(KNOWN_ADDRESS, "1"))

    assert result["status"] == "seen_in_mempool"
    assert result["mempool_count"] == 1


def test_check_payment_partial(monkeypatch):
    install_payment_fakes(monkeypatch, {"confirmed": 25, "unconfirmed": 0})

    result = asyncio.run(check_payment(KNOWN_ADDRESS, "0.000001"))

    assert result["status"] == "partial"
    assert result["received_confirmed_sats"] == 25


def test_check_payment_paid_unconfirmed(monkeypatch):
    install_payment_fakes(monkeypatch, {"confirmed": 0, "unconfirmed": 100})

    result = asyncio.run(check_payment(KNOWN_ADDRESS, "0.000001"))

    assert result["status"] == "paid_unconfirmed"
    assert result["received_unconfirmed_sats"] == 100


def test_check_payment_paid_confirmed(monkeypatch):
    install_payment_fakes(monkeypatch, {"confirmed": 100, "unconfirmed": 0})

    result = asyncio.run(check_payment(KNOWN_ADDRESS, "0.000001"))

    assert result["status"] == "paid_confirmed"
    assert result["confirmations_required"] == 3


def test_check_payment_overpaid(monkeypatch):
    install_payment_fakes(monkeypatch, {"confirmed": 150, "unconfirmed": 0})

    result = asyncio.run(check_payment(KNOWN_ADDRESS, "0.000001"))

    assert result["status"] == "overpaid"
    assert result["overpaid_by_sats"] == 50
    assert result["payment_state"] == "confirmed"


def test_check_payment_expired(monkeypatch):
    install_payment_fakes(monkeypatch, {"confirmed": 0, "unconfirmed": 0})
    expires_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 60))

    result = asyncio.run(check_payment(KNOWN_ADDRESS, "1", expires_at=expires_at))

    assert result["status"] == "waiting"
    assert result["expired"] is True
