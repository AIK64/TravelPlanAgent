from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from travel_agent.domain.models import (
    ItemType,
    PlanCandidate,
    POI,
    TripSpec,
    ValidationResult,
    Violation,
    ViolationSeverity,
)


def _normalize(value: str) -> str:
    return value.strip().casefold()


def validate_candidate(
    trip: TripSpec,
    candidate: PlanCandidate,
    pois: list[POI],
) -> ValidationResult:
    violations: list[Violation] = []
    poi_by_id = {poi.id: poi for poi in pois}
    all_items = [item for day in candidate.days for item in day.items]
    activities = [item for item in all_items if item.type == ItemType.ACTIVITY]

    if not activities:
        violations.append(
            Violation(
                type="empty_plan",
                severity=ViolationSeverity.ERROR,
                message="计划中没有任何活动",
                repair_hint="增加可用时间、预算或候选地点",
            )
        )

    scheduled_names = {_normalize(item.name) for item in activities}
    for required in trip.must_visit:
        normalized = _normalize(required)
        if not any(normalized in name or name in normalized for name in scheduled_names):
            violations.append(
                Violation(
                    type="missing_must_visit",
                    severity=ViolationSeverity.ERROR,
                    message=f"必去地点未安排：{required}",
                    repair_hint="调整每日活动上限或放宽其他约束",
                )
            )

    arrival_buffer = trip.arrival.at + timedelta(minutes=60)
    departure_buffer = trip.departure.at - timedelta(minutes=90)

    for day in candidate.days:
        ordered = sorted(day.items, key=lambda item: item.start_at)
        previous = None
        for item in ordered:
            if previous and item.start_at < previous.end_at:
                violations.append(
                    Violation(
                        type="time_overlap",
                        severity=ViolationSeverity.ERROR,
                        day=day.date,
                        entity_ids=[value for value in [previous.poi_id, item.poi_id] if value],
                        message=f"{previous.name} 与 {item.name} 时间重叠",
                        repair_hint="重新排序或减少活动",
                    )
                )
            previous = item

            if day.date == trip.arrival.at.date() and item.start_at < arrival_buffer:
                violations.append(
                    Violation(
                        type="arrival_buffer",
                        severity=ViolationSeverity.ERROR,
                        day=day.date,
                        entity_ids=[item.poi_id] if item.poi_id else [],
                        message=f"{item.name} 安排在到达缓冲时间之前",
                    )
                )
            if day.date == trip.departure.at.date() and item.end_at > departure_buffer:
                violations.append(
                    Violation(
                        type="departure_buffer",
                        severity=ViolationSeverity.ERROR,
                        day=day.date,
                        entity_ids=[item.poi_id] if item.poi_id else [],
                        message=f"{item.name} 结束时间侵占返程缓冲",
                    )
                )

            if item.poi_id and item.poi_id in poi_by_id:
                poi = poi_by_id[item.poi_id]
                timezone = item.start_at.tzinfo
                opening = datetime.combine(day.date, poi.opening_window.start, tzinfo=timezone)
                closing = datetime.combine(day.date, poi.opening_window.end, tzinfo=timezone)
                if item.start_at < opening or item.end_at > closing:
                    violations.append(
                        Violation(
                            type="outside_opening_hours",
                            severity=ViolationSeverity.ERROR,
                            day=day.date,
                            entity_ids=[poi.id],
                            message=f"{poi.name} 的访问时间不在营业时间内",
                            repair_hint="调整访问时间或移动到其他日期",
                        )
                    )

        if day.walking_distance_meters > trip.mobility.max_daily_walking_meters:
            violations.append(
                Violation(
                    type="walking_limit",
                    severity=ViolationSeverity.ERROR,
                    day=day.date,
                    message=(
                        f"预计步行 {day.walking_distance_meters} 米，超过上限 "
                        f"{trip.mobility.max_daily_walking_meters} 米"
                    ),
                    repair_hint="减少跨区域活动或降低每日活动数量",
                )
            )

        activity_minutes = sum(
            int((item.end_at - item.start_at).total_seconds() / 60)
            for item in ordered
            if item.type == ItemType.ACTIVITY
        )
        if activity_minutes > trip.mobility.max_daily_activity_minutes:
            violations.append(
                Violation(
                    type="activity_time_limit",
                    severity=ViolationSeverity.ERROR,
                    day=day.date,
                    message=(
                        f"活动时间 {activity_minutes} 分钟，超过每日上限 "
                        f"{trip.mobility.max_daily_activity_minutes} 分钟"
                    ),
                    repair_hint="减少活动或移动到其他日期",
                )
            )

    actual_cost = sum(
        (item.estimated_cost for item in activities), start=Decimal("0")
    )
    if trip.total_budget is not None and actual_cost > trip.total_budget:
        violations.append(
            Violation(
                type="budget_exceeded",
                severity=ViolationSeverity.ERROR,
                message=f"预计费用 {actual_cost} 元，超过预算 {trip.total_budget} 元",
                repair_hint="减少收费活动或提高预算",
            )
        )

    return ValidationResult(
        valid=not any(item.severity == ViolationSeverity.ERROR for item in violations),
        violations=violations,
    )
