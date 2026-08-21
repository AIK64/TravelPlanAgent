from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal
from statistics import mean

from travel_agent.domain.models import (
    Coordinate,
    DayPlan,
    ItemType,
    PlanCandidate,
    PlanItem,
    PlanMetrics,
    PlanningAssumption,
    PlanningPOI,
    TripSpec,
)
from travel_agent.domain.tool_models import (
    RouteMode,
    RouteQuery,
    RouteResult,
    ValueSource,
    route_key,
)
from travel_agent.planning.drafts import (
    CandidateDraft,
    DraftDay,
    MissingPlanningPOI,
    collect_route_queries,
)


def _normalize(value: str) -> str:
    return value.strip().casefold()


class MissingRouteResult(LookupError):
    """物化日程所需的标准化路线结果不存在。"""

    def __init__(self, missing_route_key: str) -> None:
        self.route_key = missing_route_key
        super().__init__(f"missing route result: {missing_route_key}")


def _route_query(
    origin: Coordinate,
    destination: Coordinate,
    origin_poi_id: str | None,
    destination_poi_id: str,
    route_strategy: int,
) -> RouteQuery:
    return RouteQuery(
        origin=origin,
        destination=destination,
        origin_poi_id=origin_poi_id,
        destination_poi_id=destination_poi_id,
        mode=RouteMode.DRIVING,
        strategy=route_strategy,
    )


def _append_unique_assumptions(
    target: list[PlanningAssumption],
    additions: list[PlanningAssumption],
) -> None:
    for assumption in additions:
        if assumption not in target:
            target.append(assumption)


def _route_provenance_fact(
    routes: list[RouteResult],
    total_travel_minutes: int,
) -> str:
    if not routes:
        return f"路线来源 无（未调用路线工具），交通 {total_travel_minutes} 分钟"
    providers = sorted({route.provider for route in routes})
    provider_text = "/".join(providers)
    if providers == ["mock"]:
        result_kind = "本地估算"
    elif "mock" in providers:
        result_kind = "含 mock 本地估算的 Provider 标准化结果"
    else:
        result_kind = "Provider 标准化结果"
    confidence = mean(route.data_confidence for route in routes)
    return (
        f"路线来源 {provider_text}（{result_kind}），"
        f"路线置信度 {confidence:.0%}，交通 {total_travel_minutes} 分钟"
    )


def _materialize_day(
    trip: TripSpec,
    draft_day: DraftDay,
    poi_by_id: dict[str, PlanningPOI],
    routes: dict[str, RouteResult],
    route_strategy: int,
) -> tuple[DayPlan, list[PlanningPOI], list[RouteResult]]:
    current_date = draft_day.date
    timezone = trip.arrival.at.tzinfo
    day_start = datetime.combine(current_date, trip.daily_start, tzinfo=timezone)
    day_end = datetime.combine(current_date, trip.daily_end, tzinfo=timezone)
    if current_date == trip.arrival.at.date():
        day_start = max(day_start, trip.arrival.at + timedelta(minutes=60))
    if current_date == trip.departure.at.date():
        day_end = min(day_end, trip.departure.at - timedelta(minutes=90))

    current_coordinate = (
        trip.accommodation.coordinate
        if trip.accommodation is not None
        else trip.arrival.coordinate
    )
    current_poi_id: str | None = None
    current_time = day_start
    items: list[PlanItem] = []
    scheduled_pois: list[PlanningPOI] = []
    used_routes: list[RouteResult] = []
    known_cost = Decimal("0")
    unknown_cost_count = 0
    travel_minutes_total = 0
    walking_total = 0
    for poi_id in draft_day.poi_ids:
        poi = poi_by_id.get(poi_id)
        if poi is None:
            raise MissingPlanningPOI(poi_id)
        query = _route_query(
            current_coordinate,
            poi.facts.coordinate,
            current_poi_id,
            poi_id,
            route_strategy,
        )
        key = route_key(query)
        route = routes.get(key)
        if route is None:
            raise MissingRouteResult(key)

        arrival_time = current_time + timedelta(minutes=route.duration_minutes)
        opening_window = poi.opening_windows[current_date]
        opening = datetime.combine(
            current_date, opening_window.start, tzinfo=timezone
        )
        closing = datetime.combine(
            current_date, opening_window.end, tzinfo=timezone
        )
        start_at = max(arrival_time, opening)
        end_at = start_at + timedelta(minutes=poi.duration_minutes)
        if end_at > day_end or end_at > closing:
            break

        walking_meters = min(round(route.distance_meters * 0.12), 2_000)
        items.append(
            PlanItem(
                type=ItemType.ACTIVITY,
                name=poi.facts.name,
                poi_id=poi.facts.id,
                start_at=start_at,
                end_at=end_at,
                travel_from_previous_minutes=route.duration_minutes,
                distance_from_previous_meters=route.distance_meters,
                estimated_cost=poi.party_cost,
                walking_distance_estimated=True,
            )
        )
        scheduled_pois.append(poi)
        used_routes.append(route)
        current_time = end_at
        current_coordinate = poi.facts.coordinate
        current_poi_id = poi.facts.id
        travel_minutes_total += route.duration_minutes
        walking_total += walking_meters
        if poi.party_cost is None:
            unknown_cost_count += 1
        else:
            known_cost += poi.party_cost

    categories = [
        category
        for poi in scheduled_pois
        for category in poi.facts.categories
    ]
    theme = (
        " / ".join(name for name, _ in Counter(categories).most_common(2))
        or "自由活动"
    )
    activity_minutes = sum(
        round((item.end_at - item.start_at).total_seconds() / 60)
        for item in items
    )
    fatigue = min(
        1.0,
        (activity_minutes + travel_minutes_total)
        / trip.mobility.max_daily_activity_minutes,
    )
    return (
        DayPlan(
            date=current_date,
            theme=theme,
            primary_area=trip.destination if items else "未安排",
            items=items,
            known_estimated_cost=known_cost,
            unknown_cost_item_count=unknown_cost_count,
            total_travel_minutes=travel_minutes_total,
            walking_distance_meters=walking_total,
            fatigue_score=round(fatigue, 3),
        ),
        scheduled_pois,
        used_routes,
    )


def _materialize_candidate(
    trip: TripSpec,
    draft: CandidateDraft,
    poi_by_id: dict[str, PlanningPOI],
    routes: dict[str, RouteResult],
    route_strategy: int,
) -> PlanCandidate:
    days: list[DayPlan] = []
    scheduled_pois: list[PlanningPOI] = []
    used_routes: list[RouteResult] = []
    assumptions: list[PlanningAssumption] = []
    walking_dates: list[date] = []
    for draft_day in draft.days:
        day, day_pois, day_routes = _materialize_day(
            trip, draft_day, poi_by_id, routes, route_strategy
        )
        days.append(day)
        scheduled_pois.extend(day_pois)
        used_routes.extend(day_routes)
        if day.items:
            walking_dates.append(day.date)
        for poi in day_pois:
            _append_unique_assumptions(assumptions, poi.assumptions)

    if used_routes:
        _append_unique_assumptions(
            assumptions,
            [
                PlanningAssumption(
                    field="walking_distance",
                    value="min(round(driving_distance_meters * 0.12), 2000)",
                    reason=(
                        "基于路线 Provider 标准化距离派生接驳步行估算，"
                        "非步行路线事实"
                    ),
                    source=ValueSource.DEFAULT,
                    affected_dates=walking_dates,
                    created_at=min(route.fetched_at for route in used_routes),
                )
            ],
        )

    interests = {_normalize(value) for value in trip.interests}
    matching = sum(
        bool(
            interests
            & {
                _normalize(value)
                for value in poi.facts.categories
            }
        )
        for poi in scheduled_pois
    )
    preference_match = matching / len(scheduled_pois) if scheduled_pois else 0.0
    category_count = len(
        {
            category
            for poi in scheduled_pois
            for category in poi.facts.categories
        }
    )
    diversity = min(1.0, category_count / 5)
    confidence_values = [poi.data_confidence for poi in scheduled_pois]
    confidence_values.extend(route.data_confidence for route in used_routes)
    confidence = mean(confidence_values) if confidence_values else 0.0
    total_travel = sum(day.total_travel_minutes for day in days)
    walking = sum(day.walking_distance_meters for day in days)
    known_cost = sum(
        (day.known_estimated_cost for day in days), start=Decimal("0")
    )
    unknown_cost_count = sum(day.unknown_cost_item_count for day in days)
    fatigue = mean([day.fatigue_score for day in days]) if days else 0.0
    metrics = PlanMetrics(
        preference_match=round(preference_match, 3),
        diversity=round(diversity, 3),
        data_confidence=round(confidence, 3),
        total_travel_minutes=total_travel,
        walking_distance_meters=walking,
        known_estimated_cost=known_cost,
        unknown_cost_item_count=unknown_cost_count,
        fatigue_score=round(fatigue, 3),
    )
    budget_risk = (
        min(1.0, float(known_cost / trip.total_budget))
        if trip.total_budget
        else 0.0
    )
    warning_types = {
        assumption.field
        for assumption in assumptions
        if assumption.source is ValueSource.DEFAULT
    }
    warning_risk = min(
        1.0,
        (len(warning_types) + int(unknown_cost_count > 0)) / 4,
    )
    score = (
        0.35 * metrics.preference_match
        + 0.20 * metrics.diversity
        + 0.15 * metrics.data_confidence
        - 0.10 * min(1.0, total_travel / max(1, trip.day_count * 180))
        - 0.10 * metrics.fatigue_score
        - 0.10 * budget_risk
        - 0.10 * warning_risk
    )
    return PlanCandidate(
        id=draft.id,
        style=draft.style,
        days=days,
        metrics=metrics,
        score=round(score, 4),
        reason_facts=[
            f"兴趣匹配度 {metrics.preference_match:.0%}",
            _route_provenance_fact(used_routes, metrics.total_travel_minutes),
            f"已知预计费用 {metrics.known_estimated_cost} 元",
            f"未知费用活动 {metrics.unknown_cost_item_count} 个",
            f"数据置信度 {metrics.data_confidence:.0%}",
        ],
        assumptions=assumptions,
    )


def materialize_candidates(
    trip: TripSpec,
    drafts: list[CandidateDraft],
    pois: list[PlanningPOI],
    routes: dict[str, RouteResult],
    route_strategy: int = 32,
) -> list[PlanCandidate]:
    """Phase 2：只消费标准化 RouteResult 物化日程与指标。"""
    poi_by_id = {poi.facts.id: poi for poi in pois}
    for query in collect_route_queries(
        trip,
        drafts,
        pois,
        route_strategy=route_strategy,
    ):
        key = route_key(query)
        if key not in routes:
            raise MissingRouteResult(key)
    return [
        _materialize_candidate(
            trip,
            draft,
            poi_by_id,
            routes,
            route_strategy,
        )
        for draft in drafts
    ]

