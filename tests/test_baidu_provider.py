from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from travel_agent.domain.models import Coordinate
from travel_agent.domain.tool_models import (
    POISearchQuery,
    RouteMode,
    RouteQuery,
    ToolErrorCategory,
)
from travel_agent.tools.errors import ToolProviderError
from travel_agent.tools.providers.baidu import (
    BaiduMapClient,
    BaiduPOIProvider,
    BaiduRouteProvider,
)


class FakeBaiduClient:
    def __init__(self, payloads: dict[str, dict[str, object]]) -> None:
        self.payloads = payloads
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    async def request_json(
        self, operation: str, path: str, params: dict[str, object]
    ) -> dict[str, object]:
        self.calls.append((operation, path, params))
        return self.payloads[path]


@pytest.mark.asyncio
async def test_baidu_poi_adapter_normalizes_facts_and_gcj02_request() -> None:
    client = FakeBaiduClient(
        {
            "/place/v2/search": {
                "results": [
                    {
                        "uid": "poi-1",
                        "name": "灵隐寺",
                        "city": "杭州市",
                        "location": {"lng": 120.101, "lat": 30.241},
                        "detail_info": {
                            "classified_poi_tag": "旅游景点;寺庙",
                            "price": "45.5",
                        },
                    },
                    {"uid": "broken", "name": "缺少坐标"},
                ]
            }
        }
    )

    result = await BaiduPOIProvider(client).search_pois(
        POISearchQuery(city="杭州", keyword="寺庙", exact_match=True, limit=5)
    )

    assert len(result) == 1
    assert result[0].id == "baidu:poi-1"
    assert result[0].categories == ["旅游景点", "寺庙"]
    assert result[0].average_cost_per_person == Decimal("45.5")
    _, path, params = client.calls[0]
    assert path == "/place/v2/search"
    assert params["ret_coordtype"] == "gcj02ll"
    assert params["city_limit"] == "true"


@pytest.mark.asyncio
async def test_baidu_route_adapter_uses_uid_and_rounds_up_duration() -> None:
    client = FakeBaiduClient(
        {
            "/directionlite/v1/driving": {
                "result": {"routes": [{"distance": 1234.4, "duration": 121}]}
            }
        }
    )
    query = RouteQuery(
        origin=Coordinate(longitude=120.1, latitude=30.2),
        destination=Coordinate(longitude=120.2, latitude=30.3),
        origin_poi_id="baidu:origin-1",
        destination_poi_id="baidu:destination-1",
        mode=RouteMode.DRIVING,
        strategy=33,
    )

    result = await BaiduRouteProvider(client).get_driving_route(query)

    assert result.distance_meters == 1234
    assert result.duration_minutes == 3
    _, path, params = client.calls[0]
    assert path == "/directionlite/v1/driving"
    assert params["origin"] == "30.200000,120.100000"
    assert params["origin_uid"] == "origin-1"
    assert params["destination_uid"] == "destination-1"
    assert params["tactics"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "category", "retryable"),
    [
        (3, ToolErrorCategory.AUTHENTICATION, False),
        (2, ToolErrorCategory.INVALID_REQUEST, False),
        (4, ToolErrorCategory.RATE_LIMIT, True),
        (1, ToolErrorCategory.UPSTREAM_UNAVAILABLE, True),
        (999, ToolErrorCategory.INVALID_RESPONSE, True),
    ],
)
async def test_baidu_client_classifies_provider_status(
    status, category, retryable
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": status, "message": "safe"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = BaiduMapClient(client, "secret-ak")
        with pytest.raises(ToolProviderError) as raised:
            await provider.request_json("poi.search", "/place/v2/search", {})
    assert raised.value.category is category
    assert raised.value.retryable is retryable
    assert "secret-ak" not in raised.value.safe_message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "category"),
    [
        (httpx.Response(429, json={}), ToolErrorCategory.RATE_LIMIT),
        (httpx.Response(503, json={}), ToolErrorCategory.UPSTREAM_UNAVAILABLE),
        (httpx.Response(200, content=b"not-json"), ToolErrorCategory.INVALID_RESPONSE),
    ],
)
async def test_baidu_client_classifies_http_and_json_failures(response, category) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        response.request = request
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = BaiduMapClient(client, "secret-ak")
        with pytest.raises(ToolProviderError) as raised:
            await provider.request_json("poi.search", "/place/v2/search", {})
    assert raised.value.category is category


@pytest.mark.asyncio
async def test_baidu_adapters_reject_invalid_shapes_and_support_walking() -> None:
    invalid_poi = FakeBaiduClient({"/place/v2/search": {"results": "invalid"}})
    with pytest.raises(ToolProviderError, match="无效响应"):
        await BaiduPOIProvider(invalid_poi).search_pois(
            POISearchQuery(city="杭州", keyword="公园")
        )

    walking = FakeBaiduClient(
        {
            "/directionlite/v1/walking": {
                "result": {"routes": [{"distance": 800, "duration": 600}]}
            }
        }
    )
    query = RouteQuery(
        origin=Coordinate(longitude=120.1, latitude=30.2),
        destination=Coordinate(longitude=120.2, latitude=30.3),
        mode=RouteMode.WALKING,
    )
    result = await BaiduRouteProvider(walking).get_walking_route(query)
    assert result.mode is RouteMode.WALKING
    assert result.duration_minutes == 10

    broken = FakeBaiduClient(
        {"/directionlite/v1/walking": {"result": {"routes": []}}}
    )
    with pytest.raises(ToolProviderError) as raised:
        await BaiduRouteProvider(broken).get_walking_route(query)
    assert raised.value.code == "route_no_data"
