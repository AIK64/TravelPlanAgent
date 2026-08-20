from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from travel_agent.domain.models import Coordinate
from travel_agent.domain.tool_models import POISearchQuery, ToolErrorCategory
from travel_agent.tools.errors import ToolProviderError
from travel_agent.tools.providers.amap import AMapClient, AMapPOIProvider


QUERY = POISearchQuery(city="杭州", keyword="博物馆", limit=10)
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "amap"


@pytest.fixture
def load_fixture() -> Callable[[str], dict[str, object]]:
    def load(name: str) -> dict[str, object]:
        return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))

    return load


def amap_poi_provider(
    payload: dict[str, object],
    *,
    status_code: int = 200,
) -> tuple[AMapPOIProvider, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status_code, json=payload)

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://restapi.amap.com",
    )
    return AMapPOIProvider(AMapClient(client, api_key="test-secret-key")), seen


@pytest.mark.asyncio
async def test_amap_poi_provider_normalizes_success(load_fixture):
    """若经纬度顺序或费用未标准化，后续路线与预算验证会使用错误事实。"""
    provider, seen = amap_poi_provider(load_fixture("poi_success.json"))

    facts = await provider.search_pois(QUERY)

    assert facts[0].id == "B0TEST001"
    assert facts[0].coordinate == Coordinate(longitude=120.123456, latitude=30.123456)
    assert facts[0].average_cost_per_person == Decimal("50")
    assert facts[0].provider == "amap"
    assert facts[0].suggested_duration_minutes is None
    assert facts[0].today_opening_window is not None
    assert set(facts[0].opening_windows_by_weekday) == {1, 2, 3, 4, 5, 6}
    assert seen[0].url.path == "/v5/place/text"
    assert seen[0].url.params["city_limit"] == "true"
    assert seen[0].url.params["show_fields"] == "business"


@pytest.mark.asyncio
async def test_amap_empty_poi_response_is_success(load_fixture):
    """高德成功但无匹配时，网关应收到空事实而非工具故障。"""
    provider, _ = amap_poi_provider(load_fixture("poi_empty.json"))

    assert await provider.search_pois(QUERY) == []


@pytest.mark.asyncio
async def test_amap_poi_provider_keeps_unparseable_cost_unknown(load_fixture):
    """若未可靠提供的费用被当作有效数值，预算约束会建立在伪事实之上。"""
    payload = load_fixture("poi_success.json")
    payload["pois"][0]["business"]["cost"] = "以现场价格为准"
    provider, _ = amap_poi_provider(payload)

    facts = await provider.search_pois(QUERY)

    assert facts[0].average_cost_per_person is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response_payload", "expected_category", "expected_retryable"),
    [
        (
            {"status": "0", "info": "INVALID_USER_KEY", "infocode": "10001"},
            ToolErrorCategory.AUTHENTICATION,
            False,
        ),
        (
            {
                "status": "0",
                "info": "DAILY_QUERY_OVER_LIMIT",
                "infocode": "10003",
            },
            ToolErrorCategory.RATE_LIMIT,
            False,
        ),
        (
            {"status": "0", "info": "SERVER_IS_BUSY", "infocode": "10016"},
            ToolErrorCategory.UPSTREAM_UNAVAILABLE,
            True,
        ),
    ],
)
async def test_amap_provider_maps_error_envelopes_without_leaking_key(
    response_payload,
    expected_category,
    expected_retryable,
):
    """若供应商失败被错分或泄露密钥，网关重试与安全日志都会失真。"""
    provider, _ = amap_poi_provider(response_payload)

    with pytest.raises(ToolProviderError) as raised:
        await provider.search_pois(QUERY)

    error = raised.value
    assert error.category is expected_category
    assert error.retryable is expected_retryable
    assert "test-secret-key" not in str(error)


@pytest.mark.asyncio
async def test_amap_provider_rejects_missing_coordinates_without_leaking_key(
    load_fixture,
):
    """若缺失坐标仍进入 State，路线工具会把损坏供应商数据当作可用事实。"""
    payload = load_fixture("poi_success.json")
    payload["pois"][0].pop("location")
    provider, _ = amap_poi_provider(payload)

    with pytest.raises(ToolProviderError) as raised:
        await provider.search_pois(QUERY)

    assert raised.value.category is ToolErrorCategory.INVALID_RESPONSE
    assert raised.value.retryable is False
    assert "test-secret-key" not in str(raised.value)


@pytest.mark.asyncio
async def test_amap_provider_maps_http_503_without_leaking_key(load_fixture):
    """若服务端短暂故障被视为永久错误，网关将错过恢复性重试。"""
    provider, _ = amap_poi_provider(load_fixture("server_busy.json"), status_code=503)

    with pytest.raises(ToolProviderError) as raised:
        await provider.search_pois(QUERY)

    assert raised.value.category is ToolErrorCategory.UPSTREAM_UNAVAILABLE
    assert raised.value.retryable is True
    assert "test-secret-key" not in str(raised.value)


@pytest.mark.asyncio
async def test_amap_provider_maps_timeout_without_leaking_key():
    """若网络超时没有可靠类型，Gateway 不能按预算进行恢复性重试。"""
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("simulated timeout", request=request)

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(timeout_handler),
        base_url="https://restapi.amap.com",
    )
    provider = AMapPOIProvider(AMapClient(client, api_key="test-secret-key"))

    with pytest.raises(ToolProviderError) as raised:
        await provider.search_pois(QUERY)

    assert raised.value.category is ToolErrorCategory.TIMEOUT
    assert raised.value.retryable is True
    assert "test-secret-key" not in str(raised.value)


@pytest.mark.asyncio
async def test_amap_provider_rejects_invalid_json_without_leaking_key():
    """若响应无法解析仍被上抛，网关会失去统一的安全错误语义。"""
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"not json")
        ),
        base_url="https://restapi.amap.com",
    )
    provider = AMapPOIProvider(AMapClient(client, api_key="test-secret-key"))

    with pytest.raises(ToolProviderError) as raised:
        await provider.search_pois(QUERY)

    assert raised.value.category is ToolErrorCategory.INVALID_RESPONSE
    assert raised.value.retryable is False
    assert "test-secret-key" not in str(raised.value)
