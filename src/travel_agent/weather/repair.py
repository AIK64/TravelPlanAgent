from __future__ import annotations

from travel_agent.domain.lifecycle_models import (
    EditOperation,
    EditOperationKind,
    EditPatch,
    LockKind,
    PlanLock,
)
from travel_agent.domain.models import PlanCandidate, PlanningPOI, TripSpec
from travel_agent.domain.weather_models import (
    DailyWeatherRisk,
    WeatherImpactResult,
    WeatherRepairAction,
    WeatherRepairActionKind,
    WeatherRepairPlan,
    WeatherRiskLevel,
)
from travel_agent.lifecycle.fingerprints import day_fingerprint


def _is_must_visit(name: str, trip: TripSpec) -> bool:
    normalized = name.strip().casefold()
    return any(
        required.strip().casefold() in normalized
        or normalized in required.strip().casefold()
        for required in trip.must_visit
    )


def build_weather_repair_plan(
    *,
    event_id: str,
    base_version_id: str,
    trip: TripSpec,
    candidate: PlanCandidate,
    impact: WeatherImpactResult,
    risks: tuple[DailyWeatherRisk, ...],
    alternatives: tuple[PlanningPOI, ...],
    locks: tuple[PlanLock, ...],
) -> WeatherRepairPlan | None:
    item_by_id = {
        item.item_id: (day.date, item)
        for day in candidate.days
        for item in day.items
        if item.item_id is not None
    }
    locked_days = {
        lock.target_id
        for lock in locks
        if lock.kind is LockKind.DAY
    }
    safe_dates = [
        item.date
        for item in risks
        if item.level is WeatherRiskLevel.NORMAL
        and item.date.isoformat() not in locked_days
    ]
    actions: list[WeatherRepairAction] = []
    alternative_index = 0
    for item_id in impact.affected_item_ids:
        located = item_by_id.get(item_id)
        if located is None:
            return None
        source_date, item = located
        if _is_must_visit(item.name, trip):
            target = next((day for day in safe_dates if day != source_date), None)
            if target is None:
                return None
            actions.append(
                WeatherRepairAction(
                    kind=WeatherRepairActionKind.MOVE_TO_SAFE_DATE,
                    item_id=item_id,
                    target_date=target,
                    evidence_codes=("must_visit_preserved", "target_weather_normal"),
                )
            )
            continue
        if alternative_index < len(alternatives):
            alternative = alternatives[alternative_index]
            alternative_index += 1
            actions.append(
                WeatherRepairAction(
                    kind=WeatherRepairActionKind.REPLACE_WITH_INDOOR,
                    item_id=item_id,
                    replacement_poi_id=alternative.facts.id,
                    replacement_poi_name=alternative.facts.name,
                    evidence_codes=("indoor_alternative",),
                )
            )
        else:
            actions.append(
                WeatherRepairAction(
                    kind=WeatherRepairActionKind.REMOVE_OPTIONAL_ITEM,
                    item_id=item_id,
                    evidence_codes=("no_safe_indoor_alternative",),
                )
            )
    if not actions or len(actions) > 3:
        return None
    preserved = {
        day.date.isoformat(): day_fingerprint(day)
        for day in candidate.days
        if day.date in impact.preserved_dates
    }
    return WeatherRepairPlan(
        event_id=event_id,
        base_version_id=base_version_id,
        affected_dates=impact.affected_dates,
        actions=tuple(actions),
        required_tool_operations=("poi.search", "route.delta"),
        preserved_day_fingerprints=preserved,
    )


def repair_plan_to_edit_patch(plan: WeatherRepairPlan) -> EditPatch:
    operations = []
    for action in plan.actions:
        if action.kind is WeatherRepairActionKind.REPLACE_WITH_INDOOR:
            operations.append(
                EditOperation(
                    kind=EditOperationKind.REPLACE_ITEM,
                    item_id=action.item_id,
                    poi_name=action.replacement_poi_name,
                    user_reason="weather_event",
                )
            )
        elif action.kind is WeatherRepairActionKind.MOVE_TO_SAFE_DATE:
            operations.append(
                EditOperation(
                    kind=EditOperationKind.MOVE_ITEM,
                    item_id=action.item_id,
                    target_date=action.target_date,
                    user_reason="weather_event",
                )
            )
        else:
            operations.append(
                EditOperation(
                    kind=EditOperationKind.REMOVE_ITEM,
                    item_id=action.item_id,
                    user_reason="weather_event",
                )
            )
    return EditPatch(operations=tuple(operations))
