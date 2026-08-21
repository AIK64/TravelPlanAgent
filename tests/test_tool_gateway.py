from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from travel_agent.config import Settings
from travel_agent.domain.models import Coordinate
from travel_agent.domain.tool_models import (
    POIFacts,
    POISearchQuery,
    RouteQuery,
    RouteResult,
    ToolCallContext,
    ToolErrorCategory,
    ToolStatus,
    route_key,
)
from travel_agent.tools.cache import AsyncTTLCache
from travel_agent.tools.errors import ToolProviderError
from travel_agent.tools.gateway import ToolGateway, build_gateway
from travel_agent.tools.retry import RetryPolicy


A = Coordinate(longitude=120.15507, latitude=30.274085)
B = Coordinate(longitude=120.13874, latitude=30.23095)
C = Coordinate(longitude=120.13800, latitude=30.22000)
D = Coordinate(longitude=120.18000, latitude=30.26000)
QUERY = POISearchQuery(city="Hangzhou", keyword="museum", exact_match=True, limit=3)
ROUTE = RouteQuery(origin=A, destination=B)
CONTEXT = ToolCallContext(thread_id="gateway-test")


def poi_facts() -> list[POIFacts]:
    return [
        POIFacts(
            id="poi-1",
            name="Museum",
            city="Hangzhou",
            coordinate=A,
            categories=["museum"],
            provider="fake",
            fetched_at=datetime.now(timezone.utc),
        )
    ]


class FakePOIProvider:
    name = "fake-poi"

    def __init__(self) -> None:
        self.calls = 0

    async def search_pois(self, query: POISearchQuery) -> list[POIFacts]:
        self.calls += 1
        return poi_facts()


class FakeRouteProvider:
    name = "fake-route"

    def __init__(self) -> None:
        self.calls = 0

    async def get_driving_route(self, query: RouteQuery) -> RouteResult:
        self.calls += 1
        return RouteResult(
            distance_meters=1000,
            duration_minutes=10,
            provider=self.name,
            data_confidence=1.0,
            fetched_at=datetime.now(timezone.utc),
        )


class AlwaysTimeoutPOIProvider(FakePOIProvider):
    async def search_pois(self, query: POISearchQuery) -> list[POIFacts]:
        self.calls += 1
        raise ToolProviderError.timeout("poi")


class TimeoutThenSuccessPOIProvider(FakePOIProvider):
    async def search_pois(self, query: POISearchQuery) -> list[POIFacts]:
        self.calls += 1
        if self.calls == 1:
            raise ToolProviderError.timeout("poi.search")
        return poi_facts()


class CoordinatedTimeoutPOIProvider(AlwaysTimeoutPOIProvider):
    def __init__(self) -> None:
        super().__init__()
        self.first_attempt_started = asyncio.Event()
        self.release_first_attempt = asyncio.Event()

    async def search_pois(self, query: POISearchQuery) -> list[POIFacts]:
        self.calls += 1
        if self.calls == 1:
            self.first_attempt_started.set()
            await self.release_first_attempt.wait()
        raise ToolProviderError.timeout("poi")


class BlockingPOIProvider(FakePOIProvider):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def search_pois(self, query: POISearchQuery) -> list[POIFacts]:
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return poi_facts()


class MeasuringRouteProvider(FakeRouteProvider):
    def __init__(self) -> None:
        super().__init__()
        self.active_calls = 0
        self.maximum_active_calls = 0

    async def get_driving_route(self, query: RouteQuery) -> RouteResult:
        self.calls += 1
        self.active_calls += 1
        self.maximum_active_calls = max(self.maximum_active_calls, self.active_calls)
        try:
            await asyncio.sleep(0.01)
            return RouteResult(
                distance_meters=1000,
                duration_minutes=10,
                provider=self.name,
                data_confidence=1.0,
                fetched_at=datetime.now(timezone.utc),
            )
        finally:
            self.active_calls -= 1


def make_gateway(
    *,
    poi_provider: FakePOIProvider | None = None,
    route_provider: FakeRouteProvider | None = None,
    max_concurrency: int = 5,
) -> ToolGateway:
    return ToolGateway(
        poi_provider=poi_provider or FakePOIProvider(),
        route_provider=route_provider or FakeRouteProvider(),
        cache=AsyncTTLCache(max_entries=10),
        retry=RetryPolicy(
            max_attempts=3,
            base_delay_seconds=0,
            max_delay_seconds=0,
            jitter=lambda: 0.0,
        ),
        semaphore=asyncio.Semaphore(max_concurrency),
        poi_cache_ttl_seconds=60,
        route_cache_ttl_seconds=60,
    )


@pytest.mark.asyncio
async def test_gateway_wraps_poi_result_and_hits_cache():
    provider = FakePOIProvider()
    gateway = make_gateway(poi_provider=provider)

    first = await gateway.search_pois([QUERY], CONTEXT)
    second = await gateway.search_pois([QUERY], CONTEXT)

    assert first[0].status is ToolStatus.SUCCESS
    assert first[0].cache_hit is False
    assert second[0].cache_hit is True
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_gateway_deduplicates_route_queries():
    provider = FakeRouteProvider()
    gateway = make_gateway(route_provider=provider)

    results = await gateway.get_routes([ROUTE, ROUTE.model_copy()], CONTEXT)

    assert list(results) == [route_key(ROUTE)]
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_gateway_returns_failed_tool_result_after_retry_exhaustion():
    provider = AlwaysTimeoutPOIProvider()

    result = (await make_gateway(poi_provider=provider).search_pois([QUERY], CONTEXT))[0]

    assert result.status is ToolStatus.FAILED
    assert result.attempt_count == 3
    assert result.error is not None
    assert result.error.category is ToolErrorCategory.TIMEOUT
    assert result.elapsed_ms is not None
    assert result.elapsed_ms >= 0
    assert provider.calls == 3


@pytest.mark.asyncio
async def test_gateway_does_not_cache_failed_poi_calls():
    provider = AlwaysTimeoutPOIProvider()
    gateway = make_gateway(poi_provider=provider)

    await gateway.search_pois([QUERY], CONTEXT)
    await gateway.search_pois([QUERY], CONTEXT)

    assert provider.calls == 6


@pytest.mark.asyncio
async def test_gateway_shares_one_failed_in_flight_retry_cycle_then_retries_new_request():
    provider = CoordinatedTimeoutPOIProvider()
    gateway = make_gateway(poi_provider=provider)

    first_request = asyncio.create_task(gateway.search_pois([QUERY], CONTEXT))
    await provider.first_attempt_started.wait()
    second_request = asyncio.create_task(gateway.search_pois([QUERY], CONTEXT))
    await asyncio.sleep(0)
    provider.release_first_attempt.set()
    first, second = await asyncio.gather(first_request, second_request)

    assert provider.calls == 3
    assert [result.status for result in first + second] == [ToolStatus.FAILED] * 2
    assert [result.attempt_count for result in first + second] == [3, 3]
    assert first[0].error == second[0].error

    await gateway.search_pois([QUERY], CONTEXT)

    assert provider.calls == 6


@pytest.mark.asyncio
async def test_gateway_cancelling_one_waiter_does_not_cancel_shared_load():
    provider = BlockingPOIProvider()
    gateway = make_gateway(poi_provider=provider)

    cancelled_waiter = asyncio.create_task(gateway.search_pois([QUERY], CONTEXT))
    await provider.started.wait()
    cancelled_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_waiter
    provider.release.set()
    await asyncio.sleep(0)

    result = await gateway.search_pois([QUERY], CONTEXT)

    assert provider.calls == 1
    assert result[0].cache_hit is True


@pytest.mark.asyncio
async def test_gateway_never_exceeds_configured_concurrency():
    provider = MeasuringRouteProvider()
    gateway = make_gateway(route_provider=provider, max_concurrency=2)
    queries = [
        ROUTE,
        RouteQuery(origin=A, destination=C),
        RouteQuery(origin=A, destination=D),
    ]

    await gateway.get_routes(queries, CONTEXT)

    assert provider.maximum_active_calls == 2


@pytest.mark.asyncio
async def test_gateway_single_flight_marks_waiting_duplicate_as_cache_hit():
    provider = MeasuringRouteProvider()
    gateway = make_gateway(route_provider=provider)

    first, second = await asyncio.gather(
        gateway.get_routes([ROUTE], CONTEXT),
        gateway.get_routes([ROUTE.model_copy()], CONTEXT),
    )

    key = route_key(ROUTE)
    assert provider.calls == 1
    assert {first[key].cache_hit, second[key].cache_hit} == {False, True}


@pytest.mark.asyncio
async def test_gateway_emits_safe_lifecycle_logs(caplog):
    provider = AlwaysTimeoutPOIProvider()
    gateway = make_gateway(poi_provider=provider)
    caplog.set_level("INFO", logger="travel_agent.tools.gateway")

    await gateway.search_pois([QUERY], CONTEXT)
    successful_gateway = make_gateway()
    await successful_gateway.search_pois([QUERY], CONTEXT)
    await successful_gateway.search_pois([QUERY], CONTEXT)

    messages = [record.getMessage() for record in caplog.records]
    expected_events = {
        "tool.started",
        "tool.retry_scheduled",
        "tool.failed",
        "tool.completed",
        "tool.cache_hit",
    }
    for event in expected_events:
        assert any(message.startswith(event) for message in messages), event
    required_fields = {
        "tool.started": {"thread_id=gateway-test", "provider=", "operation="},
        "tool.retry_scheduled": {
            "thread_id=gateway-test",
            "provider=",
            "operation=",
            "attempt=",
            "next_attempt=",
            "delay_seconds=",
        },
        "tool.completed": {
            "thread_id=gateway-test",
            "provider=",
            "operation=",
            "attempt_count=",
            "elapsed_ms=",
        },
        "tool.cache_hit": {
            "thread_id=gateway-test",
            "provider=",
            "operation=",
            "attempt_count=",
            "elapsed_ms=",
        },
        "tool.failed": {
            "thread_id=gateway-test",
            "provider=",
            "operation=",
            "attempt_count=3",
            "elapsed_ms=",
            "category=timeout",
            "code=timeout",
            "retryable=True",
        },
    }
    for event, fields in required_fields.items():
        event_messages = [message for message in messages if message.startswith(event)]
        assert any(all(field in message for field in fields) for message in event_messages), event
    assert all("thread_id=gateway-test" in message for message in messages)
    assert all("keyword" not in message and "museum" not in message for message in messages)


@pytest.mark.asyncio
async def test_gateway_logs_retry_then_completion_and_second_call_cache_hit(caplog):
    """错误日志顺序或缓存字段会让一次恢复性 Tool Use 无法从轨迹中还原。"""
    provider = TimeoutThenSuccessPOIProvider()
    gateway = make_gateway(poi_provider=provider)
    thread_id = "retry-cache-observability"
    context = ToolCallContext(thread_id=thread_id)
    caplog.set_level("INFO", logger="travel_agent.tools.gateway")

    first = (await gateway.search_pois([QUERY], context))[0]
    second = (await gateway.search_pois([QUERY], context))[0]

    assert provider.calls == 2
    assert first.status is ToolStatus.SUCCESS
    assert first.attempt_count == 2
    assert first.cache_hit is False
    assert second.status is ToolStatus.SUCCESS
    assert second.attempt_count == 0
    assert second.cache_hit is True

    messages = [
        record.getMessage()
        for record in caplog.records
        if f"thread_id={thread_id}" in record.getMessage()
    ]
    assert [message.split(maxsplit=1)[0] for message in messages] == [
        "tool.started",
        "tool.retry_scheduled",
        "tool.completed",
        "tool.started",
        "tool.cache_hit",
    ]
    assert "attempt=1" in messages[0]
    assert "attempt=1" in messages[1]
    assert "next_attempt=2" in messages[1]
    assert "attempt_count=2" in messages[2]
    assert "cache_hit=false" in messages[2]
    assert "cache_hit=true" in messages[4]


@pytest.mark.asyncio
async def test_build_gateway_uses_settings_concurrency_limit():
    provider = MeasuringRouteProvider()
    gateway = build_gateway(
        Settings(
            tool_max_concurrency=1,
            tool_backoff_base_seconds=0.01,
            tool_max_backoff_seconds=0.01,
        ),
        FakePOIProvider(),
        provider,
    )
    queries = [
        ROUTE,
        RouteQuery(origin=A, destination=C),
        RouteQuery(origin=A, destination=D),
    ]

    await gateway.get_routes(queries, CONTEXT)

    assert provider.maximum_active_calls == 1
