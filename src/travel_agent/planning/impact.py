from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from travel_agent.domain.models import DayPlan, PlanningPOI, TripSpec
from travel_agent.domain.tool_models import (
    RouteMode,
    RouteQuery,
    RouteResult,
    route_key,
)
from travel_agent.planning.drafts import CandidateDraft, collect_route_queries


def day_fingerprint(day: DayPlan) -> str:
    payload = day.model_dump_json(exclude_none=False)
    return sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class RouteDelta:
    required_queries: tuple[RouteQuery, ...]
    missing_queries: tuple[RouteQuery, ...]
    reused_route_keys: tuple[str, ...]


def collect_route_delta(
    trip: TripSpec,
    draft: CandidateDraft,
    pois: list[PlanningPOI],
    routes: dict[str, RouteResult],
    *,
    route_strategy: int,
    route_mode: RouteMode = RouteMode.DRIVING,
    route_modes: tuple[RouteMode, ...] | None = None,
    max_walking_leg_meters: int = 1_500,
) -> RouteDelta:
    required = tuple(
        collect_route_queries(
            trip,
            [draft],
            pois,
            route_strategy=route_strategy,
            route_mode=route_mode,
            route_modes=route_modes,
            max_walking_leg_meters=max_walking_leg_meters,
        )
    )
    missing = tuple(query for query in required if route_key(query) not in routes)
    reused = tuple(
        route_key(query) for query in required if route_key(query) in routes
    )
    return RouteDelta(
        required_queries=required,
        missing_queries=missing,
        reused_route_keys=reused,
    )


def invalidated_route_keys(
    trip: TripSpec,
    before: CandidateDraft,
    after: CandidateDraft,
    pois: list[PlanningPOI],
    *,
    route_strategy: int,
    route_mode: RouteMode = RouteMode.DRIVING,
    route_modes: tuple[RouteMode, ...] | None = None,
    max_walking_leg_meters: int = 1_500,
) -> tuple[str, ...]:
    before_keys = {
        route_key(query)
        for query in collect_route_queries(
            trip,
            [before],
            pois,
            route_strategy=route_strategy,
            route_mode=route_mode,
            route_modes=route_modes,
            max_walking_leg_meters=max_walking_leg_meters,
        )
    }
    after_keys = {
        route_key(query)
        for query in collect_route_queries(
            trip,
            [after],
            pois,
            route_strategy=route_strategy,
            route_mode=route_mode,
            route_modes=route_modes,
            max_walking_leg_meters=max_walking_leg_meters,
        )
    }
    return tuple(sorted(before_keys - after_keys))
