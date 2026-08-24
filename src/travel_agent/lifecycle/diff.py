from __future__ import annotations

from travel_agent.domain.lifecycle_models import (
    DayMetricDiff,
    ItemDiff,
    PlanDiff,
    RouteDiff,
    TimeDiff,
)
from travel_agent.domain.models import PlanCandidate


def _index(candidate: PlanCandidate) -> dict[str, tuple[object, int, object]]:
    indexed = {}
    for day in candidate.days:
        for position, item in enumerate(day.items):
            if item.item_id is None:
                raise ValueError("versioned plan items require item_id")
            indexed[item.item_id] = (day.date, position, item)
    return indexed


def build_plan_diff(
    before: PlanCandidate,
    after: PlanCandidate,
    *,
    from_version_id: str,
    to_id: str,
    soft_quality_before: float | None = None,
    soft_quality_after: float | None = None,
) -> PlanDiff:
    old = _index(before)
    new = _index(after)
    added = []
    removed = []
    moved = []
    reordered = []
    time_changes = []
    route_changes = []
    for item_id in sorted(new.keys() - old.keys()):
        day, position, item = new[item_id]
        added.append(
            ItemDiff(item_id=item_id, name=item.name, to_date=day, to_index=position)
        )
    for item_id in sorted(old.keys() - new.keys()):
        day, position, item = old[item_id]
        removed.append(
            ItemDiff(item_id=item_id, name=item.name, from_date=day, from_index=position)
        )
    for item_id in sorted(old.keys() & new.keys()):
        old_day, old_pos, old_item = old[item_id]
        new_day, new_pos, new_item = new[item_id]
        if old_day != new_day:
            moved.append(
                ItemDiff(
                    item_id=item_id,
                    name=new_item.name,
                    from_date=old_day,
                    to_date=new_day,
                    from_index=old_pos,
                    to_index=new_pos,
                )
            )
        elif old_pos != new_pos:
            reordered.append(
                ItemDiff(
                    item_id=item_id,
                    name=new_item.name,
                    from_date=old_day,
                    to_date=new_day,
                    from_index=old_pos,
                    to_index=new_pos,
                )
            )
        if old_item.start_at != new_item.start_at or old_item.end_at != new_item.end_at:
            time_changes.append(
                TimeDiff(
                    item_id=item_id,
                    before_start=old_item.start_at,
                    after_start=new_item.start_at,
                    before_end=old_item.end_at,
                    after_end=new_item.end_at,
                )
            )
        if (
            old_item.travel_from_previous_minutes
            != new_item.travel_from_previous_minutes
            or old_item.distance_from_previous_meters
            != new_item.distance_from_previous_meters
        ):
            route_changes.append(
                RouteDiff(
                    item_id=item_id,
                    before_minutes=old_item.travel_from_previous_minutes,
                    after_minutes=new_item.travel_from_previous_minutes,
                    before_meters=old_item.distance_from_previous_meters,
                    after_meters=new_item.distance_from_previous_meters,
                )
            )
    old_days = {day.date: day for day in before.days}
    new_days = {day.date: day for day in after.days}
    day_changes = []
    for day in sorted(old_days.keys() & new_days.keys()):
        left = old_days[day]
        right = new_days[day]
        if (
            left.total_travel_minutes != right.total_travel_minutes
            or left.walking_distance_meters != right.walking_distance_meters
            or left.known_estimated_cost != right.known_estimated_cost
        ):
            day_changes.append(
                DayMetricDiff(
                    day=day,
                    travel_minutes_delta=right.total_travel_minutes
                    - left.total_travel_minutes,
                    walking_meters_delta=right.walking_distance_meters
                    - left.walking_distance_meters,
                    known_cost_delta=float(
                        right.known_estimated_cost - left.known_estimated_cost
                    ),
                )
            )
    return PlanDiff(
        from_version_id=from_version_id,
        to_id=to_id,
        added_items=tuple(added),
        removed_items=tuple(removed),
        moved_items=tuple(moved),
        reordered_items=tuple(reordered),
        time_changes=tuple(time_changes),
        route_changes=tuple(route_changes),
        day_metric_changes=tuple(day_changes),
        hard_status_before=(
            before.validation.status.value if before.validation is not None else None
        ),
        hard_status_after=(
            after.validation.status.value if after.validation is not None else None
        ),
        soft_quality_before=soft_quality_before,
        soft_quality_after=soft_quality_after,
    )

