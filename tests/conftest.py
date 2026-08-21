from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, time, timezone, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from travel_agent.app import create_app
from travel_agent.config import Settings
from travel_agent.domain.models import (
    Coordinate,
    LocationAnchor,
    MobilityConstraints,
    Pace,
    TransportAnchor,
    TripSpec,
)
from travel_agent.domain.tool_models import (
    POIFacts,
    POISearchQuery,
    RouteQuery,
    RouteResult,
    ToolErrorCategory,
    ToolErrorInfo,
    ToolResult,
)
from travel_agent.graph.workflow import build_workflow
from travel_agent.planning.defaults import POIDefaultPolicy
from travel_agent.domain.tool_models import UnknownFactPolicy
from travel_agent.tools.cache import AsyncTTLCache
from travel_agent.tools.errors import ToolUnavailableError
from travel_agent.tools.gateway import ToolGateway
from travel_agent.tools.providers.mock import MockPOIProvider, MockRouteProvider
from travel_agent.tools.retry import RetryPolicy


CHINA_TZ = timezone(timedelta(hours=8))


@pytest.fixture
def client():
    with TestClient(create_app(Settings.from_env({}))) as test_client:
        yield test_client


@pytest.fixture
def tool_failure_client(monkeypatch):
    class ToolFailureRuntime:
        async def plan(self, _request, thread_id):
            error = ToolErrorInfo(
                category=ToolErrorCategory.TIMEOUT,
                code="tool_unavailable",
                operation="search_pois",
                retryable=True,
                safe_message="地图服务暂时不可用，请稍后重试",
            )
            result = ToolResult[object].failed(provider="amap", error=error)
            raise ToolUnavailableError.from_result(result, thread_id=thread_id)

        async def close(self):
            return None

    async def runtime_factory(_settings):
        return ToolFailureRuntime()

    monkeypatch.setattr("travel_agent.api.routes.uuid4", lambda: "api-tool-failure")
    application = create_app(
        Settings.from_env({}), runtime_factory=runtime_factory
    )
    with TestClient(application) as test_client:
        yield test_client


class RecordingMockPOIProvider(MockPOIProvider):
    def __init__(self) -> None:
        self.calls: list[POISearchQuery] = []

    async def search_pois(self, query: POISearchQuery) -> list[POIFacts]:
        self.calls.append(query)
        return await super().search_pois(query)


class RecordingMockRouteProvider(MockRouteProvider):
    def __init__(self) -> None:
        self.calls: list[RouteQuery] = []

    async def get_driving_route(self, query: RouteQuery) -> RouteResult:
        self.calls.append(query)
        return await super().get_driving_route(query)


@dataclass(frozen=True)
class WorkflowHarness:
    workflow: object
    gateway: ToolGateway
    poi_provider: RecordingMockPOIProvider
    route_provider: RecordingMockRouteProvider


def make_gateway(
    *,
    poi_provider=None,
    route_provider=None,
    max_attempts: int = 1,
    route_cache_ttl_seconds: int = 60,
) -> ToolGateway:
    return ToolGateway(
        poi_provider=poi_provider or RecordingMockPOIProvider(),
        route_provider=route_provider or RecordingMockRouteProvider(),
        cache=AsyncTTLCache(max_entries=100),
        retry=RetryPolicy(
            max_attempts=max_attempts,
            base_delay_seconds=0,
            max_delay_seconds=0,
            jitter=lambda: 0.0,
        ),
        semaphore=asyncio.Semaphore(5),
        poi_cache_ttl_seconds=60,
        route_cache_ttl_seconds=route_cache_ttl_seconds,
    )


@pytest.fixture
def workflow_harness() -> WorkflowHarness:
    poi_provider = RecordingMockPOIProvider()
    route_provider = RecordingMockRouteProvider()
    gateway = make_gateway(
        poi_provider=poi_provider,
        route_provider=route_provider,
    )
    return WorkflowHarness(
        workflow=build_workflow(
            gateway,
            POIDefaultPolicy(UnknownFactPolicy.ASSUME_WITH_WARNING),
        ),
        gateway=gateway,
        poi_provider=poi_provider,
        route_provider=route_provider,
    )


@pytest.fixture
def mock_workflow(workflow_harness):
    return workflow_harness.workflow


@pytest.fixture
def gateway_factory():
    return make_gateway


@pytest.fixture
def hangzhou_trip() -> TripSpec:
    return TripSpec(
        destination="杭州",
        start_date=date(2026, 10, 2),
        end_date=date(2026, 10, 4),
        travelers=3,
        arrival=TransportAnchor(
            name="杭州东站",
            at=datetime(2026, 10, 2, 10, 30, tzinfo=CHINA_TZ),
            coordinate=Coordinate(longitude=120.2120, latitude=30.2909),
        ),
        departure=TransportAnchor(
            name="杭州东站",
            at=datetime(2026, 10, 4, 19, 0, tzinfo=CHINA_TZ),
            coordinate=Coordinate(longitude=120.2120, latitude=30.2909),
        ),
        accommodation=LocationAnchor(
            name="西湖东侧",
            coordinate=Coordinate(longitude=120.1650, latitude=30.2500),
        ),
        total_budget=Decimal("1500"),
        interests=["自然", "美食", "人文"],
        avoid=["高强度"],
        must_visit=["灵隐寺"],
        pace=Pace.RELAXED,
        mobility=MobilityConstraints(
            max_daily_walking_meters=6_000,
            max_daily_activity_minutes=360,
            needs_frequent_rest=True,
        ),
        daily_start=time(9, 0),
        daily_end=time(20, 0),
    )

