import asyncio
import time

import pytest

from app.services import payment_service
from app.services.payment_service import (
    InvalidPaymentAmountError,
    STATUS_EXPLANATIONS,
    check_payment,
    format_pepew_amount_from_sats,
    payment_status_explanation,
    parse_pepew_amount,
)

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


def test_format_pepew_amount_from_sats_uses_integer_math():
    assert format_pepew_amount_from_sats(100000000, decimals=8) == "1"
    assert format_pepew_amount_from_sats(100000001, decimals=8) == "1.00000001"
    assert format_pepew_amount_from_sats(1, decimals=8) == "0.00000001"
    assert format_pepew_amount_from_sats(100000000000, decimals=8) == "1000"


@pytest.mark.parametrize("amount", ["0", "-1", "abc", "1.000000001"])
def test_parse_pepew_amount_rejects_invalid_values(amount):
    with pytest.raises(InvalidPaymentAmountError):
        parse_pepew_amount(amount, decimals=8)


def test_check_payment_waiting(monkeypatch):
    install_payment_fakes(monkeypatch, {"confirmed": 0, "unconfirmed": 0})

    result = asyncio.run(check_payment(KNOWN_ADDRESS, "1"))

    assert result["status"] == "waiting"
    assert result["requested_amount"] == "1"
    assert result["requested_sats"] == 100000000
    assert result["confirmed_received"] == "0"
    assert result["confirmed_received_sats"] == 0
    assert result["mempool_received"] == "0"
    assert result["mempool_received_sats"] == 0
    assert result["total_received"] == "0"
    assert result["total_received_sats"] == 0
    assert result["received_confirmed_sats"] == 0
    assert result["received_unconfirmed_sats"] == 0
    assert result["amount_pepew"] == "1"
    assert result["pepew_decimals"] == 8
    assert result["explorer_address_url"] == f"https://explorer.pepepow.net/address/{KNOWN_ADDRESS}"
    assert result["status_explanation"] == "No matching payment has been seen yet."
    assert result["message"] == result["status_explanation"]


def test_check_payment_normalized_sats_fields_are_integers(monkeypatch):
    install_payment_fakes(monkeypatch, {"confirmed": 25, "unconfirmed": 75})

    result = asyncio.run(check_payment(KNOWN_ADDRESS, "0.000001"))

    for field in [
        "requested_sats",
        "confirmed_received_sats",
        "mempool_received_sats",
        "total_received_sats",
    ]:
        assert isinstance(result[field], int)
    assert result["total_received_sats"] == 100


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
    assert result["expires_in"] == 0
    assert result["status_explanation"] == "This display monitor has expired."


def test_status_explanations_exist_for_all_known_statuses():
    for status in [
        "waiting",
        "seen_in_mempool",
        "partial",
        "paid_unconfirmed",
        "paid_confirmed",
        "overpaid",
        "expired",
        "error",
    ]:
        assert STATUS_EXPLANATIONS[status]
        if status not in {"expired", "error"}:
            assert payment_status_explanation(status) == STATUS_EXPLANATIONS[status]

    assert payment_status_explanation("waiting", expired=True) == STATUS_EXPLANATIONS["expired"]
