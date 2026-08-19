"""有界、异步安全的 TTL 缓存。"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Generic, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class CacheLookup(Generic[T]):
    """缓存查询结果及该值的单调时钟过期时间。"""

    value: T
    hit: bool
    expires_at_monotonic: float


@dataclass(frozen=True)
class _CacheEntry(Generic[T]):
    value: T
    expires_at: float
    inserted_at: float


class AsyncTTLCache(Generic[T]):
    """按 key 去重加载、按 TTL 过期且容量有界的异步缓存。"""

    def __init__(
        self,
        max_entries: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        self._max_entries = max_entries
        self._clock = clock
        self._entries: dict[object, _CacheEntry[T]] = {}
        self._locks: dict[object, asyncio.Lock] = {}
        self._lock_users: dict[object, int] = {}

    async def get_or_load(
        self,
        key: object,
        ttl_seconds: float,
        loader: Callable[[], Awaitable[T]],
        should_cache: Callable[[T], bool] = lambda value: True,
    ) -> CacheLookup[T]:
        now = self._clock()
        entry = self._entries.get(key)
        if entry is not None:
            if now < entry.expires_at:
                return CacheLookup(
                    value=entry.value,
                    hit=True,
                    expires_at_monotonic=entry.expires_at,
                )
            del self._entries[key]

        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        self._lock_users[key] = self._lock_users.get(key, 0) + 1
        try:
            async with lock:
                now = self._clock()
                entry = self._entries.get(key)
                if entry is not None:
                    if now < entry.expires_at:
                        return CacheLookup(
                            value=entry.value,
                            hit=True,
                            expires_at_monotonic=entry.expires_at,
                        )
                    del self._entries[key]

                value = await loader()
                loaded_at = self._clock()
                expires_at = loaded_at + ttl_seconds
                if should_cache(value):
                    self._remove_expired(loaded_at)
                    self._entries[key] = _CacheEntry(
                        value=value,
                        expires_at=expires_at,
                        inserted_at=loaded_at,
                    )
                    self._evict_if_needed()
                return CacheLookup(
                    value=value,
                    hit=False,
                    expires_at_monotonic=expires_at,
                )
        finally:
            users = self._lock_users[key] - 1
            if users == 0:
                del self._lock_users[key]
                if self._locks.get(key) is lock:
                    del self._locks[key]
            else:
                self._lock_users[key] = users

    def _remove_expired(self, now: float | None = None) -> None:
        if now is None:
            now = self._clock()
        expired_keys = [
            key for key, entry in self._entries.items() if now >= entry.expires_at
        ]
        for key in expired_keys:
            del self._entries[key]

    def _evict_if_needed(self) -> None:
        while len(self._entries) > self._max_entries:
            key, _ = min(
                self._entries.items(),
                key=lambda item: (item[1].expires_at, item[1].inserted_at),
            )
            del self._entries[key]
