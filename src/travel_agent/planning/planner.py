from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
from statistics import mean

from travel_agent.domain.models import (
    DayPlan,
    ItemType,
    PlanCandidate,
    PlanItem,
    PlanMetrics,
    PlanStyle,
    POI,
    TripSpec,
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
    return [
        _build_candidate(trip, pois, style, replan_round)
        for style in PlanStyle
    ]

