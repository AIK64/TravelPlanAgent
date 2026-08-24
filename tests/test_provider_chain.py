from __future__ import annotations

from datetime import datetime, timezone

import pytest

from travel_agent.domain.models import Coordinate
from travel_agent.domain.tool_models import (
    POIFacts,
    POISearchQuery,
    ToolErrorCategory,
)
from travel_agent.tools.errors import ToolProviderError
from travel_agent.tools.providers.chain import POIProviderChain


class StubPOIProvider:
    def __init__(self, name: str, result):
        self.name = name
        self.result = result
        self.calls = 0

    async def search_pois(self, query):
        self.calls += 1
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def _poi(provider: str) -> POIFacts:
    return POIFacts(
        id=f"{provider}-1",
        name="测试景点",
        city="杭州",
        coordinate=Coordinate(longitude=120.1, latitude=30.2),
        categories=["景点"],
        provider=provider,
        fetched_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_provider_chain_falls_back_on_timeout():
    primary = StubPOIProvider("primary", ToolProviderError.timeout("poi.search"))
    fallback = StubPOIProvider("fallback", [_poi("fallback")])
    chain = POIProviderChain((primary, fallback))

    result = await chain.search_pois(POISearchQuery(city="杭州", keyword="景点"))

    assert result[0].provider == "fallback"
    assert primary.calls == fallback.calls == 1


@pytest.mark.asyncio
async def test_provider_chain_does_not_fallback_on_authentication_error():
    primary = StubPOIProvider(
        "primary", ToolProviderError.authentication("poi.search")
    )
    fallback = StubPOIProvider("fallback", [_poi("fallback")])
    chain = POIProviderChain((primary, fallback))

    with pytest.raises(ToolProviderError) as captured:
        await chain.search_pois(POISearchQuery(city="杭州", keyword="景点"))

    assert captured.value.category is ToolErrorCategory.AUTHENTICATION
    assert fallback.calls == 0


@pytest.mark.asyncio
async def test_provider_chain_treats_empty_as_valid_no_data():
    primary = StubPOIProvider("primary", [])
    fallback = StubPOIProvider("fallback", [_poi("fallback")])
    chain = POIProviderChain((primary, fallback))

    result = await chain.search_pois(POISearchQuery(city="杭州", keyword="不存在"))

    assert result == []
    assert fallback.calls == 0
