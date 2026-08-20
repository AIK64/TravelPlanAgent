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
        self._inflight: dict[
            object, asyncio.Task[tuple[CacheLookup[T], bool]]
        ] = {}

    async def get_or_load(
        self,
        key: object,
        ttl_seconds: float,
        loader: Callable[[], Awaitable[T]],
        should_cache: Callable[[T], bool] = lambda value: True,
    ) -> CacheLookup[T]:
        entry = self._get_live_entry(key)
        if entry is not None:
            return CacheLookup(
                value=entry.value,
                hit=True,
                expires_at_monotonic=entry.expires_at,
            )

        task = self._inflight.get(key)
        is_loader = task is None
        if task is None:
            task = asyncio.create_task(
                self._load_and_cache(key, ttl_seconds, loader, should_cache)
            )
            self._inflight[key] = task
        lookup, cached = await asyncio.shield(task)

        if is_loader or not cached:
            return lookup
        return CacheLookup(
            value=lookup.value,
            hit=True,
            expires_at_monotonic=lookup.expires_at_monotonic,
        )

    async def _load_and_cache(
        self,
        key: object,
        ttl_seconds: float,
        loader: Callable[[], Awaitable[T]],
        should_cache: Callable[[T], bool],
    ) -> tuple[CacheLookup[T], bool]:
        try:
            value = await loader()
            loaded_at = self._clock()
            expires_at = loaded_at + ttl_seconds
            cached = should_cache(value)
            if cached:
                self._remove_expired(loaded_at)
                self._entries[key] = _CacheEntry(
                    value=value,
                    expires_at=expires_at,
                    inserted_at=loaded_at,
                )
                self._evict_if_needed()
            return (
                CacheLookup(
                    value=value,
                    hit=False,
                    expires_at_monotonic=expires_at,
                ),
                cached,
            )
        finally:
            task = asyncio.current_task()
            if self._inflight.get(key) is task:
                del self._inflight[key]

    def _get_live_entry(self, key: object) -> _CacheEntry[T] | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if self._clock() < entry.expires_at:
            return entry
        del self._entries[key]
        return None

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
