import time
from dataclasses import dataclass
from typing import Any


@dataclass
class CacheEntry:
    value: Any
    expires_at: float


class TTLCache:
    def __init__(self, max_items: int | None = None) -> None:
        if max_items is not None and max_items <= 0:
            raise ValueError("max_items must be greater than zero.")
        self._items: dict[str, CacheEntry] = {}
        self._max_items = max_items

    def _prune_expired(self, now: float | None = None) -> None:
        current = time.time() if now is None else now
        expired = [key for key, entry in self._items.items() if entry.expires_at < current]
        for key in expired:
            self._items.pop(key, None)

    def get(self, key: str) -> Any | None:
        entry = self._items.get(key)
        if entry is None:
            return None
        if entry.expires_at < time.time():
            self._items.pop(key, None)
            return None
        return entry.value

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        ttl = int(ttl_seconds)
        if ttl <= 0:
            self._items.pop(key, None)
            return

        now = time.time()
        self._prune_expired(now)

        if self._max_items is not None and key not in self._items and len(self._items) >= self._max_items:
            # Entries normally share similar TTLs, so evicting the soonest-expiring
            # item also approximates oldest-first without another index structure.
            evict_key = min(self._items, key=lambda item_key: self._items[item_key].expires_at)
            self._items.pop(evict_key, None)

        self._items[key] = CacheEntry(value=value, expires_at=now + ttl)

    def clear(self) -> None:
        self._items.clear()

    def __len__(self) -> int:
        self._prune_expired()
        return len(self._items)
