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
    PlanStyle,
    PlanningAssumption,
    PlanningPOI,
    POI,
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
from travel_agent.planning.routing import estimate_route


STYLE_ACTIVITY_LIMITS = {
    PlanStyle.RELAXED: 2,
    PlanStyle.BALANCED: 3,
    PlanStyle.EXPLORATION: 4,
}


def _normalize(value: str) -> str:
    return value.strip().casefold()


def _poi_preference_score(poi: POI, trip: TripSpec, replan_round: int) -> float:
    interests = {_normalize(value) for value in trip.interests}
    avoid = {_normalize(value) for value in trip.avoid}
    categories = {_normalize(value) for value in poi.categories}
    tags = {_normalize(value) for value in poi.suitability_tags}
    name = _normalize(poi.name)

    score = 1.0
    score += 4.0 * len(interests & (categories | tags))
    score -= 5.0 * len(avoid & (categories | tags))
    if any(_normalize(required) in name or name in _normalize(required) for required in trip.must_visit):
        score += 100.0
    if trip.mobility.needs_frequent_rest and "适老" in poi.suitability_tags:
        score += 2.0
    if replan_round:
        score -= float(poi.estimated_cost / Decimal("100")) * replan_round
    return score


def _select_pois(
    trip: TripSpec,
    pois: list[POI],
    style: PlanStyle,
    replan_round: int,
) -> list[POI]:
    per_day_limit = max(1, STYLE_ACTIVITY_LIMITS[style] - replan_round)
    total_limit = min(len(pois), trip.day_count * per_day_limit)
    ranked = sorted(
        pois,
        key=lambda poi: (
            _poi_preference_score(poi, trip, replan_round),
            poi.data_confidence,
            -float(poi.estimated_cost),
        ),
        reverse=True,
    )
    return ranked[:total_limit]


def _order_nearest(start, pois: list[POI]) -> list[POI]:
    remaining = list(pois)
    ordered: list[POI] = []
    current = start
    while remaining:
        next_poi = min(remaining, key=lambda poi: estimate_route(current, poi.coordinate)[0])
        ordered.append(next_poi)
        remaining.remove(next_poi)
        current = next_poi.coordinate
    return ordered


def _schedule_day(
    trip: TripSpec,
    day_index: int,
    pois: list[POI],
) -> DayPlan:
    current_date = trip.start_date + timedelta(days=day_index)
    timezone = trip.arrival.at.tzinfo
    day_start = datetime.combine(current_date, trip.daily_start, tzinfo=timezone)
    day_end = datetime.combine(current_date, trip.daily_end, tzinfo=timezone)

    if current_date == trip.arrival.at.date():
        day_start = max(day_start, trip.arrival.at + timedelta(minutes=60))
    if current_date == trip.departure.at.date():
        day_end = min(day_end, trip.departure.at - timedelta(minutes=90))

    start_coordinate = (
        trip.accommodation.coordinate if trip.accommodation else trip.arrival.coordinate
    )
    ordered = _order_nearest(start_coordinate, pois)
    current_coordinate = start_coordinate
    current_time = day_start
    items: list[PlanItem] = []
    cost = Decimal("0")
    travel_minutes_total = 0
    walking_total = 0

    must_visit = {_normalize(name) for name in trip.must_visit}
    for poi in ordered:
        distance, travel_minutes, walking_meters = estimate_route(
            current_coordinate, poi.coordinate
        )
        arrival_time = current_time + timedelta(minutes=travel_minutes)
        opening = datetime.combine(current_date, poi.opening_window.start, tzinfo=timezone)
        closing = datetime.combine(current_date, poi.opening_window.end, tzinfo=timezone)
        start_at = max(arrival_time, opening)
        end_at = start_at + timedelta(minutes=poi.estimated_duration_minutes)
        required = any(value in _normalize(poi.name) for value in must_visit)

        if (end_at > day_end or end_at > closing) and not required:
            continue

        items.append(
            PlanItem(
                type=ItemType.ACTIVITY,
                name=poi.name,
                poi_id=poi.id,
                start_at=start_at,
                end_at=end_at,
                travel_from_previous_minutes=travel_minutes,
                distance_from_previous_meters=distance,
                estimated_cost=poi.estimated_cost,
            )
        )
        current_time = end_at
        current_coordinate = poi.coordinate
        cost += poi.estimated_cost
        travel_minutes_total += travel_minutes
        walking_total += walking_meters

    categories = [category for poi in ordered for category in poi.categories]
    theme = " / ".join(name for name, _ in Counter(categories).most_common(2)) or "自由活动"
    primary_area = "杭州市区" if items else "未安排"
    activity_minutes = sum(
        round((item.end_at - item.start_at).total_seconds() / 60) for item in items
    )
    fatigue = min(
        1.0,
        (activity_minutes + travel_minutes_total) / trip.mobility.max_daily_activity_minutes,
    )
    return DayPlan(
        date=current_date,
        theme=theme,
        primary_area=primary_area,
        items=items,
        estimated_cost=cost,
        total_travel_minutes=travel_minutes_total,
        walking_distance_meters=walking_total,
        fatigue_score=round(fatigue, 3),
    )


def _build_candidate(
    trip: TripSpec,
    pois: list[POI],
    style: PlanStyle,
    replan_round: int,
) -> PlanCandidate:
    selected = _select_pois(trip, pois, style, replan_round)
    day_buckets: list[list[POI]] = [[] for _ in range(trip.day_count)]
    for index, poi in enumerate(selected):
        day_buckets[index % trip.day_count].append(poi)

    days = [
        _schedule_day(trip, day_index, day_buckets[day_index])
        for day_index in range(trip.day_count)
    ]
    scheduled_ids = {
        item.poi_id for day in days for item in day.items if item.poi_id is not None
    }
    scheduled = [poi for poi in selected if poi.id in scheduled_ids]
    interests = {_normalize(value) for value in trip.interests}
    matching = sum(
        1
        for poi in scheduled
        if interests & {_normalize(value) for value in poi.categories + poi.suitability_tags}
    )
    preference_match = matching / len(scheduled) if scheduled else 0.0
    category_count = len({category for poi in scheduled for category in poi.categories})
    diversity = min(1.0, category_count / 5)
    confidence = mean([poi.data_confidence for poi in scheduled]) if scheduled else 0.0
    total_travel = sum(day.total_travel_minutes for day in days)
    walking = sum(day.walking_distance_meters for day in days)
    cost = sum((day.estimated_cost for day in days), start=Decimal("0"))
    fatigue = mean([day.fatigue_score for day in days]) if days else 0.0

    metrics = PlanMetrics(
        preference_match=round(preference_match, 3),
        diversity=round(diversity, 3),
        data_confidence=round(confidence, 3),
        total_travel_minutes=total_travel,
        walking_distance_meters=walking,
        estimated_cost=cost,
        fatigue_score=round(fatigue, 3),
    )
    budget_risk = (
        min(1.0, float(cost / trip.total_budget)) if trip.total_budget else 0.0
    )
    score = (
        0.35 * metrics.preference_match
        + 0.20 * metrics.diversity
        + 0.15 * metrics.data_confidence
        - 0.10 * min(1.0, total_travel / max(1, trip.day_count * 180))
        - 0.10 * metrics.fatigue_score
        - 0.10 * budget_risk
    )
    return PlanCandidate(
        id=f"{style.value}-r{replan_round}",
        style=style,
        days=days,
        metrics=metrics,
        score=round(score, 4),
        reason_facts=[
            f"兴趣匹配度 {metrics.preference_match:.0%}",
            f"预计交通 {metrics.total_travel_minutes} 分钟",
            f"预计费用 {metrics.estimated_cost} 元",
            f"数据置信度 {metrics.data_confidence:.0%}",
        ],
    )


def generate_candidates(
    trip: TripSpec,
    pois: list[POI],
    replan_round: int = 0,
) -> list[PlanCandidate]:
    """仅供 Task 11 接线前的 v0.1 Graph 兼容桥。"""
    return [
        _build_candidate(trip, pois, style, replan_round)
        for style in PlanStyle
    ]


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
) -> RouteQuery:
    return RouteQuery(
        origin=origin,
        destination=destination,
        origin_poi_id=origin_poi_id,
        destination_poi_id=destination_poi_id,
        mode=RouteMode.DRIVING,
        strategy=32,
    )


def _append_unique_assumptions(
    target: list[PlanningAssumption],
    additions: list[PlanningAssumption],
) -> None:
    for assumption in additions:
        if assumption not in target:
            target.append(assumption)


def _materialize_day(
    trip: TripSpec,
    draft_day: DraftDay,
    poi_by_id: dict[str, PlanningPOI],
    routes: dict[str, RouteResult],
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
    must_visit = {_normalize(name) for name in trip.must_visit}

    for poi_id in draft_day.poi_ids:
        poi = poi_by_id.get(poi_id)
        if poi is None:
            raise MissingPlanningPOI(poi_id)
        query = _route_query(
            current_coordinate,
            poi.facts.coordinate,
            current_poi_id,
            poi_id,
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
        normalized_name = _normalize(poi.facts.name)
        required = any(
            value in normalized_name or normalized_name in value
            for value in must_visit
        )
        if (end_at > day_end or end_at > closing) and not required:
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
) -> PlanCandidate:
    days: list[DayPlan] = []
    scheduled_pois: list[PlanningPOI] = []
    used_routes: list[RouteResult] = []
    assumptions: list[PlanningAssumption] = []
    walking_dates: list[date] = []
    for draft_day in draft.days:
        day, day_pois, day_routes = _materialize_day(
            trip, draft_day, poi_by_id, routes
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
                    reason="基于真实驾车距离派生接驳步行估算，非步行路线事实",
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
            f"真实驾车路线交通 {metrics.total_travel_minutes} 分钟",
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
) -> list[PlanCandidate]:
    """Phase 2：只消费标准化 RouteResult 物化日程与指标。"""
    poi_by_id = {poi.facts.id: poi for poi in pois}
    for query in collect_route_queries(trip, drafts, pois):
        key = route_key(query)
        if key not in routes:
            raise MissingRouteResult(key)
    return [
        _materialize_candidate(trip, draft, poi_by_id, routes)
        for draft in drafts
    ]

