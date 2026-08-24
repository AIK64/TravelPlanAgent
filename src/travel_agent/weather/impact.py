from __future__ import annotations

from travel_agent.domain.lifecycle_models import LockKind, PlanLock
from travel_agent.domain.models import PlanCandidate, PlanningPOI
from travel_agent.domain.weather_models import (
    ChangeEvent,
    DailyWeatherRisk,
    ExposureKind,
    WeatherImpactResult,
    WeatherRiskLevel,
)
from travel_agent.weather.exposure import classify_planning_poi


def analyze_weather_impact(
    *,
    event: ChangeEvent,
    candidate: PlanCandidate,
    planning_pois: tuple[PlanningPOI, ...],
    risks: tuple[DailyWeatherRisk, ...],
    locks: tuple[PlanLock, ...],
    max_affected_days: int = 2,
) -> WeatherImpactResult:
    pois = {item.facts.id: item for item in planning_pois}
    risk_by_date = {item.date: item for item in risks}
    affected_items: set[str] = set()
    affected_dates: set = set()
    unknown_items: set[str] = set()
    reasons: set[str] = set()

    for day in candidate.days:
        if day.date not in event.affected_dates:
            continue
        risk = risk_by_date.get(day.date)
        if risk is None or risk.level is WeatherRiskLevel.NORMAL:
            continue
        if risk.level is WeatherRiskLevel.UNKNOWN:
            unknown_items.update(
                item.item_id for item in day.items if item.item_id is not None
            )
            reasons.add("weather_risk_unknown")
            continue
        for item in day.items:
            if item.item_id is None or item.poi_id is None:
                continue
            poi = pois.get(item.poi_id)
            if poi is None:
                unknown_items.add(item.item_id)
                reasons.add("planning_poi_missing")
                continue
            exposure = classify_planning_poi(poi, item_id=item.item_id)
            if exposure.exposure in {ExposureKind.OUTDOOR, ExposureKind.MIXED}:
                affected_items.add(item.item_id)
                affected_dates.add(day.date)
                reasons.add(f"exposure_{exposure.exposure.value}")
            elif exposure.exposure is ExposureKind.UNKNOWN:
                unknown_items.add(item.item_id)
                reasons.add("activity_exposure_unknown")

    lock_conflicts: set[str] = set()
    affected_iso = {item.isoformat() for item in affected_dates}
    for lock in locks:
        if lock.kind is LockKind.DAY and lock.target_id in affected_iso:
            lock_conflicts.add(lock.lock_id)
        if lock.kind is LockKind.ITEM and lock.target_id in affected_items:
            lock_conflicts.add(lock.lock_id)

    all_dates = {item.date for item in candidate.days}
    attention = bool(lock_conflicts or unknown_items)
    if len(affected_dates) > max_affected_days:
        attention = True
        reasons.add("affected_day_budget_exceeded")
    if len(affected_items) > 3:
        attention = True
        reasons.add("operation_budget_exceeded")
    return WeatherImpactResult(
        event_id=event.event_id,
        affected_dates=tuple(sorted(affected_dates)),
        affected_item_ids=tuple(sorted(affected_items)),
        preserved_dates=tuple(sorted(all_dates - affected_dates)),
        lock_conflicts=tuple(sorted(lock_conflicts)),
        unknown_exposure_item_ids=tuple(sorted(unknown_items)),
        requires_user_attention=attention,
        reasons=tuple(sorted(reasons)),
    )
