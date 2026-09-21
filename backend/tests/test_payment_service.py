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
    payment_service.clear_payment_cache()

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
    assert result["status_explanation"] == "Current address balance is below the requested amount."
    assert result["message"] == result["status_explanation"]
    
    # Assert new balance fields
    assert result["confirmed_balance"] == "0"
    assert result["confirmed_balance_sats"] == 0
    assert result["mempool_balance"] == "0"
    assert result["mempool_balance_sats"] == 0
    assert result["total_visible_balance"] == "0"
    assert result["total_visible_balance_sats"] == 0


def test_check_payment_normalized_sats_fields_are_integers(monkeypatch):
    install_payment_fakes(monkeypatch, {"confirmed": 25, "unconfirmed": 75})

    result = asyncio.run(check_payment(KNOWN_ADDRESS, "0.000001"))

    for field in [
        "requested_sats",
        "confirmed_balance_sats",
        "mempool_balance_sats",
        "total_visible_balance_sats",
        "confirmed_received_sats",
        "mempool_received_sats",
        "total_received_sats",
    ]:
        assert isinstance(result[field], int)
    assert result["total_visible_balance_sats"] == 100
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


def test_payment_check_short_cache_reuses_one_upstream_observation(monkeypatch):
    payment_service.clear_payment_cache()
    settings = payment_service.get_settings().model_copy(update={"cache_payment_seconds": 5})
    monkeypatch.setattr(payment_service, "get_settings", lambda: settings)

    calls = {"identify": 0, "balance": 0, "history": 0, "mempool": 0}

    async def fake_identify(_client):
        calls["identify"] += 1

    async def fake_balance(_client, _scripthash):
        calls["balance"] += 1
        return {"confirmed": 100, "unconfirmed": 0}

    async def fake_history(_client, _scripthash):
        calls["history"] += 1
        return [{"tx_hash": "a" * 64, "height": 100}]

    async def fake_mempool(_client, _scripthash):
        calls["mempool"] += 1
        return []

    monkeypatch.setattr(payment_service, "ElectrumXClient", FakeElectrumXClient)
    monkeypatch.setattr(payment_service, "_identify_client", fake_identify)
    monkeypatch.setattr(payment_service, "scripthash_get_balance", fake_balance)
    monkeypatch.setattr(payment_service, "scripthash_get_history", fake_history)
    monkeypatch.setattr(payment_service, "scripthash_get_mempool", fake_mempool)

    async def run_checks():
        first = await check_payment(KNOWN_ADDRESS, "0.000001")
        second = await check_payment(KNOWN_ADDRESS, "0.000001")
        return first, second

    first, second = asyncio.run(run_checks())

    assert calls == {"identify": 1, "balance": 1, "history": 1, "mempool": 1}
    assert first["cache"]["hit"] is False
    assert second["cache"]["hit"] is True
    assert second["observed_at"] == first["observed_at"]


def test_payment_check_concurrent_requests_share_inflight_fetch(monkeypatch):
    payment_service.clear_payment_cache()
    settings = payment_service.get_settings().model_copy(update={"cache_payment_seconds": 5})
    monkeypatch.setattr(payment_service, "get_settings", lambda: settings)

    calls = {"balance": 0, "history": 0, "mempool": 0}

    async def fake_identify(_client):
        return None

    async def fake_balance(_client, _scripthash):
        calls["balance"] += 1
        await asyncio.sleep(0.01)
        return {"confirmed": 0, "unconfirmed": 100}

    async def fake_history(_client, _scripthash):
        calls["history"] += 1
        return []

    async def fake_mempool(_client, _scripthash):
        calls["mempool"] += 1
        return [{"tx_hash": "b" * 64, "height": 0}]

    monkeypatch.setattr(payment_service, "ElectrumXClient", FakeElectrumXClient)
    monkeypatch.setattr(payment_service, "_identify_client", fake_identify)
    monkeypatch.setattr(payment_service, "scripthash_get_balance", fake_balance)
    monkeypatch.setattr(payment_service, "scripthash_get_history", fake_history)
    monkeypatch.setattr(payment_service, "scripthash_get_mempool", fake_mempool)

    async def run_checks():
        return await asyncio.gather(
            check_payment(KNOWN_ADDRESS, "0.000001"),
            check_payment(KNOWN_ADDRESS, "0.000001"),
        )

    results = asyncio.run(run_checks())

    assert calls == {"balance": 1, "history": 1, "mempool": 1}
    assert sum(1 for result in results if result["cache"]["shared_inflight"]) == 1
    assert all(result["status"] == "paid_unconfirmed" for result in results)


def test_payment_cache_can_be_disabled(monkeypatch):
    payment_service.clear_payment_cache()
    settings = payment_service.get_settings().model_copy(update={"cache_payment_seconds": 0})
    monkeypatch.setattr(payment_service, "get_settings", lambda: settings)

    calls = {"balance": 0}

    async def fake_identify(_client):
        return None

    async def fake_balance(_client, _scripthash):
        calls["balance"] += 1
        return {"confirmed": 0, "unconfirmed": 0}

    async def fake_history(_client, _scripthash):
        return []

    async def fake_mempool(_client, _scripthash):
        return []

    monkeypatch.setattr(payment_service, "ElectrumXClient", FakeElectrumXClient)
    monkeypatch.setattr(payment_service, "_identify_client", fake_identify)
    monkeypatch.setattr(payment_service, "scripthash_get_balance", fake_balance)
    monkeypatch.setattr(payment_service, "scripthash_get_history", fake_history)
    monkeypatch.setattr(payment_service, "scripthash_get_mempool", fake_mempool)

    async def run_checks():
        await check_payment(KNOWN_ADDRESS, "1")
        await check_payment(KNOWN_ADDRESS, "1")

    asyncio.run(run_checks())

    assert calls["balance"] == 2
