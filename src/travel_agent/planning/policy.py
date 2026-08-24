"""不含凭证、可安全注入 Graph 的规划执行策略。"""

from __future__ import annotations

from dataclasses import dataclass

from travel_agent.domain.tool_models import RouteMode


@dataclass(frozen=True, slots=True)
class PlanningPolicy:
    poi_query_limit: int = 10
    poi_candidate_limit: int = 12
    route_strategy: int = 32
    max_walking_leg_meters: int = 1_500
    use_real_walking_routes: bool = True
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
        if self.max_walking_leg_meters <= 0:
            raise ValueError("MAX_WALKING_LEG_METERS must be positive")

    @property
    def route_modes(self) -> tuple[RouteMode, ...]:
        if self.use_real_walking_routes:
            return (RouteMode.DRIVING, RouteMode.WALKING)
        return (RouteMode.DRIVING,)

    def route_strategy_for(self, mode: RouteMode) -> int:
        return self.route_strategy if mode is RouteMode.DRIVING else 0
