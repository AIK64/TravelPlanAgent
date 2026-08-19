from __future__ import annotations

import asyncio

import pytest

from travel_agent.tools.cache import AsyncTTLCache


async def async_value(value):
    return value


@pytest.mark.asyncio
async def test_cache_loads_once_then_hits():
    calls = 0

    async def loader():
        nonlocal calls
        calls += 1
        return "value"

    cache = AsyncTTLCache[str](max_entries=10, clock=lambda: 100.0)
    first = await cache.get_or_load("k", 30, loader)
    second = await cache.get_or_load("k", 30, loader)

    assert (first.hit, second.hit, calls) == (False, True, 1)


@pytest.mark.asyncio
async def test_concurrent_same_key_uses_single_loader():
    calls = 0

    async def loader():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return "value"

    cache = AsyncTTLCache[str](max_entries=10)
    results = await asyncio.gather(
        *[cache.get_or_load("same", 30, loader) for _ in range(8)]
    )

    assert calls == 1
    assert sum(result.hit for result in results) == 7


@pytest.mark.asyncio
async def test_expired_value_is_reloaded():
    now = [100.0]
    calls = 0

    async def loader():
        nonlocal calls
        calls += 1
        return calls

    cache = AsyncTTLCache[int](max_entries=2, clock=lambda: now[0])
    assert (await cache.get_or_load("k", 5, loader)).value == 1
    now[0] = 106.0
    assert (await cache.get_or_load("k", 5, loader)).value == 2


@pytest.mark.asyncio
async def test_loader_failure_is_not_cached():
    calls = 0

    async def loader():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("boom")
        return "ok"

    cache = AsyncTTLCache[str](max_entries=2)
    with pytest.raises(RuntimeError, match="boom"):
        await cache.get_or_load("k", 5, loader)

    assert (await cache.get_or_load("k", 5, loader)).value == "ok"
    assert calls == 2


@pytest.mark.asyncio
async def test_capacity_evicts_oldest_entry():
    now = [100.0]
    cache = AsyncTTLCache[str](max_entries=1, clock=lambda: now[0])
    await cache.get_or_load("first", 30, lambda: async_value("one"))
    now[0] = 101.0
    await cache.get_or_load("second", 30, lambda: async_value("two"))

    reloaded = await cache.get_or_load("first", 30, lambda: async_value("new"))

    assert reloaded.hit is False
    assert reloaded.value == "new"
