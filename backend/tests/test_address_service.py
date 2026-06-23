import asyncio

from app.services import address_service

KNOWN_ADDRESS = "PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb"
KNOWN_SCRIPTHASH = "3c5cab2c9f663ed292a0fdff3e681569c3f5d6741ca4b2ca4ea6281b9d4d4298"


class FakeSettings:
    electrumx_host = "127.0.0.1"
    electrumx_port = 50001
    electrumx_use_ssl = False
    electrumx_timeout = 1.0
    cache_balance_seconds = 15
    cache_history_seconds = 30


async def fake_balance(client, scripthash):
    assert scripthash == KNOWN_SCRIPTHASH
    return {"confirmed": 1000, "unconfirmed": -50}


async def fake_history(client, scripthash):
    assert scripthash == KNOWN_SCRIPTHASH
    return [{"tx_hash": "b" * 64, "height": 123}]


async def fake_mempool(client, scripthash):
    assert scripthash == KNOWN_SCRIPTHASH
    return [{"tx_hash": "c" * 64, "height": 0, "fee": 10}]


def test_get_address_summary(monkeypatch):
    address_service.clear_address_caches()
    monkeypatch.setattr(address_service, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(address_service, "scripthash_get_balance", fake_balance)
    monkeypatch.setattr(address_service, "scripthash_get_history", fake_history)
    monkeypatch.setattr(address_service, "scripthash_get_mempool", fake_mempool)

    result = asyncio.run(address_service.get_address_summary(KNOWN_ADDRESS))

    assert result["ok"] is True
    assert result["address"] == KNOWN_ADDRESS
    assert result["scripthash"] == KNOWN_SCRIPTHASH
    assert result["balance"] == {"confirmed": 1000, "unconfirmed": -50}
    assert result["history_count"] == 1
    assert result["mempool_count"] == 1
    assert result["cache"]["hit"] is False


def test_get_address_history(monkeypatch):
    address_service.clear_address_caches()
    monkeypatch.setattr(address_service, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(address_service, "scripthash_get_history", fake_history)
    monkeypatch.setattr(address_service, "scripthash_get_mempool", fake_mempool)

    result = asyncio.run(address_service.get_address_history(KNOWN_ADDRESS))

    assert result["ok"] is True
    assert result["history"] == [{"tx_hash": "b" * 64, "height": 123}]
    assert result["mempool"] == [{"tx_hash": "c" * 64, "height": 0, "fee": 10}]
    assert result["history_count"] == 1
    assert result["mempool_count"] == 1


def test_address_summary_cache(monkeypatch):
    calls = {"balance": 0}
    address_service.clear_address_caches()
    monkeypatch.setattr(address_service, "get_settings", lambda: FakeSettings())

    async def counted_balance(client, scripthash):
        calls["balance"] += 1
        return {"confirmed": 1, "unconfirmed": 0}

    monkeypatch.setattr(address_service, "scripthash_get_balance", counted_balance)
    monkeypatch.setattr(address_service, "scripthash_get_history", fake_history)
    monkeypatch.setattr(address_service, "scripthash_get_mempool", fake_mempool)

    first = asyncio.run(address_service.get_address_summary(KNOWN_ADDRESS))
    second = asyncio.run(address_service.get_address_summary(KNOWN_ADDRESS))

    assert first["cache"]["hit"] is False
    assert second["cache"]["hit"] is True
    assert calls["balance"] == 1
