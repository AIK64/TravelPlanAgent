"""显式启用的 AMap 连通性检查；常规测试绝不访问网络。"""

from __future__ import annotations

import os

import httpx
import pytest

from travel_agent.domain.models import Coordinate
from travel_agent.domain.tool_models import POISearchQuery, RouteQuery
from travel_agent.tools.providers.amap import AMapClient, AMapPOIProvider, AMapRouteProvider


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_AMAP_LIVE") != "1" or not os.getenv("AMAP_API_KEY"),
    reason="set RUN_AMAP_LIVE=1 and AMAP_API_KEY to run live AMap smoke tests",
)


@pytest.mark.asyncio
async def test_amap_hangzhou_poi_and_route_smoke():
    """只断言规范化 schema，不输出或断言 API key。"""
    async with httpx.AsyncClient(base_url="https://restapi.amap.com") as client:
        amap_client = AMapClient(client, api_key=os.environ["AMAP_API_KEY"])
        pois = await AMapPOIProvider(amap_client).search_pois(
            POISearchQuery(city="杭州", keyword="西湖", limit=1)
        )
        assert pois
        route = await AMapRouteProvider(amap_client).get_driving_route(
            RouteQuery(
                origin=Coordinate(longitude=120.15507, latitude=30.274085),
                destination=Coordinate(longitude=120.13874, latitude=30.23095),
            )
        )
    assert route.distance_meters > 0
    assert route.duration_minutes > 0
