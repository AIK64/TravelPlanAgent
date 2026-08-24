from __future__ import annotations

from typing import Protocol, runtime_checkable

from travel_agent.domain.tool_models import POIFacts, POISearchQuery, RouteQuery, RouteResult


@runtime_checkable
class POIProvider(Protocol):
    name: str

    async def search_pois(self, query: POISearchQuery) -> list[POIFacts]:
        raise NotImplementedError


@runtime_checkable
class RouteProvider(Protocol):
    name: str

    async def get_driving_route(self, query: RouteQuery) -> RouteResult:
        raise NotImplementedError

    async def get_walking_route(self, query: RouteQuery) -> RouteResult:
        raise NotImplementedError
