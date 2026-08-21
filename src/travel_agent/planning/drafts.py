from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from math import inf

from pydantic import BaseModel, ConfigDict

from travel_agent.domain.models import Coordinate, PlanStyle, PlanningPOI, POI, TripSpec
from travel_agent.domain.tool_models import POIFacts, RouteMode, RouteQuery, route_key
from travel_agent.planning.routing import haversine_distance_meters


STYLE_ACTIVITY_LIMITS = {
    PlanStyle.RELAXED: 2,
    PlanStyle.BALANCED: 3,
    PlanStyle.EXPLORATION: 4,
}


class MissingPlanningPOI(LookupError):
    """Draft 引用了当前规划上下文中不存在的 POI。"""

    def __init__(self, poi_id: str) -> None:
        self.poi_id = poi_id
        super().__init__(f"missing planning POI: {poi_id}")


class DraftDay(BaseModel):
    model_config = ConfigDict(frozen=True)

    date: date
    poi_ids: tuple[str, ...]


class CandidateDraft(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    style: PlanStyle
    days: tuple[DraftDay, ...]


def _normalize(value: str) -> str:
    return value.strip().casefold()


def _facts(poi: PlanningPOI | POI) -> POIFacts | POI:
    return poi.facts if isinstance(poi, PlanningPOI) else poi


def _party_cost(poi: PlanningPOI | POI) -> Decimal | None:
    if isinstance(poi, PlanningPOI):
        return poi.party_cost
    return poi.estimated_cost


def _confidence(poi: PlanningPOI | POI) -> float:
    return poi.data_confidence


def _is_must_visit(poi: PlanningPOI | POI, trip: TripSpec) -> bool:
    name = _normalize(_facts(poi).name)
    return any(
        _normalize(required) in name or name in _normalize(required)
        for required in trip.must_visit
    )


def _poi_preference_score(
    poi: PlanningPOI | POI,
    trip: TripSpec,
    replan_round: int,
) -> float:
    facts = _facts(poi)
    interests = {_normalize(value) for value in trip.interests}
    avoid = {_normalize(value) for value in trip.avoid}
    categories = {_normalize(value) for value in facts.categories}
    tags = {
        _normalize(value)
        for value in getattr(facts, "suitability_tags", [])
    }
    score = 1.0
    score += 4.0 * len(interests & (categories | tags))
    score -= 5.0 * len(avoid & (categories | tags))
    if _is_must_visit(poi, trip):
        score += 100.0
    if trip.mobility.needs_frequent_rest and "适老" in categories | tags:
        score += 2.0
    cost = _party_cost(poi)
    if replan_round and cost is not None:
        score -= float(cost / Decimal("100")) * replan_round
    return score


def _select_pois(
    trip: TripSpec,
    pois: list[PlanningPOI],
    style: PlanStyle,
    replan_round: int,
) -> list[PlanningPOI]:
    per_day_limit = max(1, STYLE_ACTIVITY_LIMITS[style] - replan_round)
    total_limit = min(len(pois), trip.day_count * per_day_limit)

    def rank_key(poi: PlanningPOI) -> tuple[float, float, float, str]:
        cost = _party_cost(poi)
        return (
            -_poi_preference_score(poi, trip, replan_round),
            -_confidence(poi),
            float(cost) if cost is not None else inf,
            poi.facts.id,
        )

    return sorted(pois, key=rank_key)[:total_limit]


def _order_nearest(
    start: Coordinate,
    pois: list[PlanningPOI],
    trip: TripSpec,
) -> list[PlanningPOI]:
    ordered: list[PlanningPOI] = []
    current = start
    for required_layer in (True, False):
        remaining = [
            poi
            for poi in pois
            if _is_must_visit(poi, trip) is required_layer
        ]
        while remaining:
            next_poi = min(
                remaining,
                key=lambda poi: (
                    haversine_distance_meters(current, poi.facts.coordinate),
                    poi.facts.id,
                ),
            )
            ordered.append(next_poi)
            remaining.remove(next_poi)
            current = next_poi.facts.coordinate
    return ordered


def prepare_candidate_drafts(
    trip: TripSpec,
    pois: list[PlanningPOI],
    replan_round: int,
) -> list[CandidateDraft]:
    """Phase 1：确定性选择与排序，不生成任何路线时间或道路距离。"""
    start_coordinate = (
        trip.accommodation.coordinate
        if trip.accommodation is not None
        else trip.arrival.coordinate
    )
    drafts: list[CandidateDraft] = []
    for style in PlanStyle:
        selected = _select_pois(trip, pois, style, replan_round)
        day_buckets: list[list[PlanningPOI]] = [
            [] for _ in range(trip.day_count)
        ]
        for index, poi in enumerate(selected):
            day_buckets[index % trip.day_count].append(poi)
        days = tuple(
            DraftDay(
                date=trip.start_date + timedelta(days=day_index),
                poi_ids=tuple(
                    poi.facts.id
                    for poi in _order_nearest(
                        start_coordinate,
                        day_buckets[day_index],
                        trip,
                    )
                ),
            )
            for day_index in range(trip.day_count)
        )
        drafts.append(
            CandidateDraft(
                id=f"{style.value}-r{replan_round}",
                style=style,
                days=days,
            )
        )
    return drafts


def collect_route_queries(
    trip: TripSpec,
    drafts: list[CandidateDraft],
    pois: list[PlanningPOI],
    route_strategy: int = 32,
) -> list[RouteQuery]:
    """收集首见优先、方向敏感的 Provider 驾车路线查询。"""
    poi_by_id = {poi.facts.id: poi for poi in pois}
    anchor = (
        trip.accommodation.coordinate
        if trip.accommodation is not None
        else trip.arrival.coordinate
    )
    queries: list[RouteQuery] = []
    seen_keys: set[str] = set()
    for draft in drafts:
        for day in draft.days:
            previous_coordinate = anchor
            previous_poi_id: str | None = None
            for poi_id in day.poi_ids:
                poi = poi_by_id.get(poi_id)
                if poi is None:
                    raise MissingPlanningPOI(poi_id)
                query = RouteQuery(
                    origin=previous_coordinate,
                    destination=poi.facts.coordinate,
                    origin_poi_id=previous_poi_id,
                    destination_poi_id=poi_id,
                    mode=RouteMode.DRIVING,
                    strategy=route_strategy,
                )
                key = route_key(query)
                if key not in seen_keys:
                    seen_keys.add(key)
                    queries.append(query)
                previous_coordinate = poi.facts.coordinate
                previous_poi_id = poi_id
    return queries
