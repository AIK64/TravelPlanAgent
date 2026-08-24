from __future__ import annotations

from travel_agent.domain.lifecycle_models import (
    EditOperationKind,
    EditPatch,
    ImpactResult,
    ImpactScope,
    PlanLock,
    LockKind,
)
from travel_agent.domain.models import PlanCandidate


def _item_locations(candidate: PlanCandidate) -> dict[str, object]:
    locations = {}
    for day in candidate.days:
        for item in day.items:
            if item.item_id:
                locations[item.item_id] = day.date
    return locations


def analyze_change_impact(
    candidate: PlanCandidate,
    patch: EditPatch,
    locks: tuple[PlanLock, ...],
    *,
    max_affected_days: int = 2,
) -> ImpactResult:
    locations = _item_locations(candidate)
    affected_dates: set = set()
    affected_items: set[str] = set()
    tool_operations: set[str] = set()
    reasons = []
    for operation in patch.operations:
        if operation.item_id:
            source_day = locations.get(operation.item_id)
            if source_day is not None:
                affected_dates.add(source_day)
                affected_items.add(operation.item_id)
        if operation.target_date is not None:
            affected_dates.add(operation.target_date)
        if operation.kind in {
            EditOperationKind.ADD_ITEM,
            EditOperationKind.REPLACE_ITEM,
        }:
            tool_operations.add("poi.resolve")
        if operation.kind is not EditOperationKind.REMOVE_ITEM:
            tool_operations.add("route.delta")
        reasons.append(operation.kind.value)

    all_dates = {day.date for day in candidate.days}
    conflicts = []
    for lock in locks:
        if lock.kind is LockKind.DAY and lock.target_id in {
            day.isoformat() for day in affected_dates
        }:
            conflicts.append(lock.lock_id)
        if lock.kind is LockKind.ITEM and lock.target_id in affected_items:
            conflicts.append(lock.lock_id)

    if len(affected_dates) > max_affected_days:
        scope = ImpactScope.REQUIRES_NEW_PLAN
        reasons.append("affected_day_budget_exceeded")
    elif len(affected_dates) > 1:
        scope = ImpactScope.MULTI_DAY
    elif affected_dates:
        scope = ImpactScope.DAY
    else:
        scope = ImpactScope.ITEM
    return ImpactResult(
        scope=scope,
        affected_dates=tuple(sorted(affected_dates)),
        affected_item_ids=tuple(sorted(affected_items)),
        preserved_dates=tuple(sorted(all_dates - affected_dates)),
        required_tool_operations=tuple(sorted(tool_operations)),
        lock_conflicts=tuple(sorted(conflicts)),
        reasons=tuple(reasons),
    )

