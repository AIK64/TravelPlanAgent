from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from travel_agent.domain.models import Coordinate
from travel_agent.domain.tool_models import (
    POIFacts,
    ToolResult,
    ValueSource,
)
from travel_agent.requirements.anchors import (
    build_anchor_search_plan,
    resolve_anchor_search_results,
)
from travel_agent.requirements.models import (
    AnchorDraft,
    RequirementDraft,
    RequirementIssueCode,
)
from travel_agent.tools.providers.mock import MockPOIProvider


CHINA_TZ = timezone(timedelta(hours=8))


def _draft() -> RequirementDraft:
    return RequirementDraft(
        destination="杭州",
        start_date=date(2026, 10, 2),
        end_date=date(2026, 10, 4),
        arrival=AnchorDraft(
            name="杭州东站",
            at=datetime(2026, 10, 2, 10, 30, tzinfo=CHINA_TZ),
        ),
        departure=AnchorDraft(
            name="杭州东站",
            at=datetime(2026, 10, 4, 19, 0, tzinfo=CHINA_TZ),
        ),
        accommodation_name="西湖东侧",
    )


def _facts(identifier: str, name: str) -> POIFacts:
    return POIFacts(
        id=identifier,
        name=name,
        city="杭州",
        coordinate=Coordinate(longitude=120.2, latitude=30.2),
        categories=["交通枢纽"],
        provider="test",
        fetched_at=datetime.now(timezone.utc),
        data_confidence=0.9,
        field_sources={"coordinate": ValueSource.PROVIDER},
    )


def test_anchor_search_plan_deduplicates_same_arrival_and_departure_name():
    plan = build_anchor_search_plan(_draft())

    assert len(plan) == 2
    assert plan[0].query.keyword == "杭州东站"
    assert plan[0].roles == ["arrival", "departure"]
    assert plan[1].query.keyword == "西湖东侧"
    assert plan[1].roles == ["accommodation"]
    assert all(item.query.exact_match for item in plan)


def test_anchor_resolution_accepts_exact_match_and_maps_all_roles():
    plan = build_anchor_search_plan(_draft())
    results = [
        ToolResult.success(data=[_facts("station", "杭州东站")], provider="test"),
        ToolResult.success(data=[_facts("hotel", "西湖东侧")], provider="test"),
    ]

    resolutions, issues = resolve_anchor_search_results(plan, results)

    assert issues == []
    assert resolutions["arrival"].poi_id == "station"
    assert resolutions["departure"].poi_id == "station"
    assert resolutions["accommodation"].poi_id == "hotel"


def test_empty_anchor_result_becomes_clarification_not_tool_failure():
    plan = build_anchor_search_plan(_draft())
    results = [
        ToolResult.success(data=[], provider="test"),
        ToolResult.success(data=[_facts("hotel", "西湖东侧")], provider="test"),
    ]

    resolutions, issues = resolve_anchor_search_results(plan, results)

    assert "arrival" not in resolutions
    assert "departure" not in resolutions
    assert {issue.field for issue in issues} == {
        "arrival.name",
        "departure.name",
    }
    assert all(issue.code is RequirementIssueCode.NOT_FOUND for issue in issues)


def test_multiple_equally_matching_anchors_require_clarification():
    plan = build_anchor_search_plan(_draft())[:1]
    results = [
        ToolResult.success(
            data=[
                _facts("station-1", "杭州东站"),
                _facts("station-2", "杭州东站"),
            ],
            provider="test",
        )
    ]

    resolutions, issues = resolve_anchor_search_results(plan, results)

    assert resolutions == {}
    assert all(issue.code is RequirementIssueCode.AMBIGUOUS for issue in issues)


@pytest.mark.asyncio
async def test_mock_provider_contains_explicit_anchor_fixtures():
    query = build_anchor_search_plan(_draft())[0].query

    facts = await MockPOIProvider().search_pois(query)

    assert [item.name for item in facts] == ["杭州东站"]
