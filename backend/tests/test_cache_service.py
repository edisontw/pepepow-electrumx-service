from app.services import cache_service
from app.services.cache_service import TTLCache


def test_ttl_cache_prunes_expired_entries_on_set(monkeypatch):
    now = {"value": 100.0}
    monkeypatch.setattr(cache_service.time, "time", lambda: now["value"])

    cache = TTLCache(max_items=2)
    cache.set("old", "value", 1)
    assert len(cache) == 1

    now["value"] = 102.0
    cache.set("new", "value", 5)

    assert cache.get("old") is None
    assert cache.get("new") == "value"
    assert len(cache) == 1


def test_ttl_cache_evicts_when_bounded(monkeypatch):
    now = {"value": 100.0}
    monkeypatch.setattr(cache_service.time, "time", lambda: now["value"])

    cache = TTLCache(max_items=2)
    cache.set("a", 1, 5)
    now["value"] = 101.0
    cache.set("b", 2, 5)
    now["value"] = 102.0
    cache.set("c", 3, 5)

    assert cache.get("a") is None
    assert cache.get("b") == 2
    assert cache.get("c") == 3
    assert len(cache) == 2


def test_ttl_cache_zero_ttl_does_not_store():
    cache = TTLCache(max_items=2)
    cache.set("a", 1, 0)

    assert cache.get("a") is None
    assert len(cache) == 0
