"""不含凭证、可安全注入 Graph 的规划执行策略。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PlanningPolicy:
    poi_query_limit: int = 10
    poi_candidate_limit: int = 12
    route_strategy: int = 32
    poi_max_queries: int = 12

    def __post_init__(self) -> None:
        if not 1 <= self.poi_query_limit <= 25:
            raise ValueError("POI_QUERY_LIMIT must be between 1 and 25")
        if not 1 <= self.poi_candidate_limit <= 100:
            raise ValueError("POI_CANDIDATE_LIMIT must be between 1 and 100")
        if not 1 <= self.poi_max_queries <= 100:
            raise ValueError("POI_MAX_QUERIES must be between 1 and 100")
        if self.route_strategy < 0:
            raise ValueError("AMAP_DRIVING_STRATEGY must be non-negative")
