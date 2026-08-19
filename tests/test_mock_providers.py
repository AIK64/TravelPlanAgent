from __future__ import annotations

import pytest

from travel_agent.domain.models import Coordinate
from travel_agent.domain.tool_models import (
    POISearchQuery,
    RouteMode,
    RouteQuery,
    ValueSource,
)
from travel_agent.tools.providers.mock import MockPOIProvider, MockRouteProvider


A = Coordinate(longitude=120.15507, latitude=30.274085)
B = Coordinate(longitude=120.13874, latitude=30.23095)


@pytest.mark.asyncio
async def test_mock_poi_provider_filters_city_and_keyword():
    provider = MockPOIProvider()

    results = await provider.search_pois(
        POISearchQuery(city="杭州", keyword="自然", limit=10)
    )

    assert results
    assert all(item.city == "杭州" for item in results)
    assert any("自然" in item.categories for item in results)
    assert all(item.provider == "mock" for item in results)


@pytest.mark.asyncio
async def test_mock_route_provider_returns_driving_result():
    result = await MockRouteProvider().get_driving_route(
        RouteQuery(origin=A, destination=B)
    )

    assert result.distance_meters > 0
    assert result.duration_minutes > 0
    assert result.mode is RouteMode.DRIVING
    assert result.provider == "mock"
    assert result.data_confidence == 0.65


@pytest.mark.asyncio
async def test_mock_poi_provider_maps_known_facts_and_seven_weekday_windows():
    result = await MockPOIProvider().search_pois(
        POISearchQuery(city="杭州市", keyword="灵隐寺", exact_match=True)
    )

    assert len(result) == 1
    facts = result[0]
    assert facts.coordinate.longitude == 120.1017
    assert facts.average_cost_per_person == 75
    assert facts.suggested_duration_minutes == 120
    assert set(facts.opening_windows_by_weekday) == set(range(7))
    assert all(
        source is ValueSource.PROVIDER for source in facts.field_sources.values()
    )


@pytest.mark.asyncio
async def test_mock_poi_provider_matches_suitability_tags_deterministically():
    provider = MockPOIProvider()
    first = await provider.search_pois(
        POISearchQuery(city="杭州", keyword="适老", limit=10)
    )
    second = await provider.search_pois(
        POISearchQuery(city="杭州", keyword="适老", limit=10)
    )

    assert [item.id for item in first] == [item.id for item in second]
    assert [item.id for item in first] == [
        "hz_west_lake",
        "hz_xixi",
        "hz_zhejiang_museum",
        "hz_tea_museum",
        "hz_botanical_garden",
    ]
