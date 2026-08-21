"""将用户偏好转换为稳定、可审计的 POI 检索意图。"""

from travel_agent.domain.models import TripSpec
from travel_agent.domain.tool_models import POISearchQuery


def build_search_plan(
    trip: TripSpec,
    per_query_limit: int = 10,
    max_queries: int = 12,
) -> list[POISearchQuery]:
    """按必去地点、兴趣和兜底景点的顺序构造去重检索计划。"""
    if not 1 <= max_queries <= 100:
        raise ValueError("max_queries must be between 1 and 100")
    seen: set[str] = set()
    queries: list[POISearchQuery] = []
    candidates = [
        *((name, True, 100) for name in trip.must_visit),
        *((interest, False, 50) for interest in trip.interests),
    ]

    for keyword, exact_match, priority in candidates:
        normalized = keyword.strip().casefold()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        queries.append(
            POISearchQuery(
                city=trip.destination,
                keyword=keyword.strip(),
                exact_match=exact_match,
                limit=per_query_limit,
                priority=priority,
            )
        )
        if len(queries) == max_queries:
            break

    if not queries:
        queries.append(
            POISearchQuery(city=trip.destination, keyword="景点", limit=per_query_limit, priority=10)
        )
    return queries
