from __future__ import annotations

import logging

from travel_agent.domain.models import PlanCandidate, PlanningPOI, TripSpec
from travel_agent.domain.tool_models import POISearchQuery, ToolCallContext, ToolStatus
from travel_agent.planning.defaults import POIDefaultPolicy
from travel_agent.planning.policy import PlanningPolicy
from travel_agent.tools.errors import ToolUnavailableError
from travel_agent.tools.gateway import ToolGateway
from travel_agent.weather.exposure import indoor_alternatives


logger = logging.getLogger(__name__)


async def resolve_indoor_alternatives(
    *,
    session_id: str,
    lifecycle_thread_id: str,
    trip: TripSpec,
    candidate: PlanCandidate,
    planning_pois: tuple[PlanningPOI, ...],
    tool_gateway: ToolGateway,
    default_policy: POIDefaultPolicy,
    planning_policy: PlanningPolicy,
    max_searches: int,
    max_alternatives: int,
    minimum_confidence: float,
) -> tuple[PlanningPOI, ...]:
    keywords = (
        "博物馆",
        "美术馆",
        "科技馆",
        "展览馆",
        "室内景点",
        "购物中心",
        "剧院",
        "文化馆",
        "图书馆",
        "商场",
    )[:max_searches]
    results = await tool_gateway.search_pois(
        [
            POISearchQuery(
                city=trip.destination,
                keyword=keyword,
                exact_match=False,
                limit=min(max_alternatives, planning_policy.poi_query_limit),
                priority=100 - index,
            )
            for index, keyword in enumerate(keywords)
        ],
        ToolCallContext(thread_id=lifecycle_thread_id),
    )

    merged = {item.facts.id: item for item in planning_pois}
    for result in results:
        if result.status is ToolStatus.FAILED:
            raise ToolUnavailableError.from_result(result, lifecycle_thread_id)
        for facts in result.data or []:
            if facts.id in merged:
                continue
            resolution = default_policy.resolve(facts, trip)
            if resolution.poi is not None:
                merged[facts.id] = resolution.poi
    used_ids = {
        item.poi_id
        for day in candidate.days
        for item in day.items
        if item.poi_id is not None
    }
    alternatives = indoor_alternatives(
        tuple(merged.values()),
        excluded_poi_ids=used_ids,
        minimum_confidence=minimum_confidence,
    )[:max_alternatives]
    logger.info(
        "weather.alternatives.searched | session_id=%s query_count=%s candidate_count=%s",
        session_id,
        len(keywords),
        len(alternatives),
    )
    return alternatives
