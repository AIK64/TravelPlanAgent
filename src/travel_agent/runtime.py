from __future__ import annotations

from dataclasses import dataclass

import httpx
from langgraph.graph.state import CompiledStateGraph

from travel_agent.config import Settings
from travel_agent.domain.models import PlanningRequest, PlanningResponse
from travel_agent.domain.tool_models import ProviderMode
from travel_agent.graph.workflow import build_workflow, run_planning
from travel_agent.planning.defaults import POIDefaultPolicy
from travel_agent.planning.policy import PlanningPolicy
from travel_agent.tools.gateway import ToolGateway, build_gateway
from travel_agent.tools.protocols import POIProvider, RouteProvider
from travel_agent.tools.providers.amap import (
    AMapClient,
    AMapPOIProvider,
    AMapRouteProvider,
)
from travel_agent.tools.providers.mock import MockPOIProvider, MockRouteProvider


@dataclass(slots=True)
class PlanningRuntime:
    """持有一套显式选定、由应用生命周期共享的规划依赖。"""

    poi_provider: POIProvider
    route_provider: RouteProvider
    gateway: ToolGateway
    workflow: CompiledStateGraph
    client: httpx.AsyncClient | None

    @classmethod
    async def create(cls, settings: Settings) -> "PlanningRuntime":
        settings.validate()
        client: httpx.AsyncClient | None = None
        try:
            if settings.provider is ProviderMode.MOCK:
                poi_provider: POIProvider = MockPOIProvider()
                route_provider: RouteProvider = MockRouteProvider()
            else:
                assert settings.amap_api_key is not None
                client = httpx.AsyncClient(base_url="https://restapi.amap.com")
                amap_client = AMapClient(
                    client,
                    api_key=settings.amap_api_key,
                    timeout_seconds=settings.tool_timeout_seconds,
                )
                poi_provider = AMapPOIProvider(amap_client)
                route_provider = AMapRouteProvider(amap_client)

            gateway = build_gateway(settings, poi_provider, route_provider)
            defaults = POIDefaultPolicy(settings.unknown_fact_policy)
            policy = PlanningPolicy(
                poi_query_limit=settings.poi_query_limit,
                poi_candidate_limit=settings.poi_candidate_limit,
                route_strategy=settings.amap_driving_strategy,
                poi_max_queries=settings.poi_max_queries,
            )
            return cls(
                poi_provider=poi_provider,
                route_provider=route_provider,
                gateway=gateway,
                workflow=build_workflow(gateway, defaults, policy),
                client=client,
            )
        except BaseException:
            if client is not None:
                await client.aclose()
            raise

    async def close(self) -> None:
        if self.client is not None:
            await self.client.aclose()

    async def plan(
        self,
        request: PlanningRequest,
        thread_id: str,
    ) -> PlanningResponse:
        return await run_planning(self.workflow, request, thread_id=thread_id)
