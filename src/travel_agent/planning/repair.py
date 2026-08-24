from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256

from travel_agent.domain.models import (
    ItemType,
    PlanCandidate,
    PlanItem,
    PlanningPOI,
    TripSpec,
)
from travel_agent.domain.repair_models import (
    CriticReport,
    RepairAction,
    RepairActionKind,
    RepairPlan,
)
from travel_agent.domain.tool_models import RouteMode
from travel_agent.planning.drafts import CandidateDraft, DraftDay
from travel_agent.planning.impact import invalidated_route_keys


def _normalize(value: str) -> str:
    return value.strip().casefold()


def _is_required_name(name: str, trip: TripSpec) -> bool:
    normalized = _normalize(name)
    return any(
        _normalize(required) in normalized or normalized in _normalize(required)
        for required in trip.must_visit
    )


def _is_required_poi(poi: PlanningPOI, trip: TripSpec) -> bool:
    return _is_required_name(poi.facts.name, trip)


def _preference_score(poi: PlanningPOI, trip: TripSpec) -> int:
    terms = {_normalize(value) for value in poi.facts.categories}
    interests = {_normalize(value) for value in trip.interests}
    avoid = {_normalize(value) for value in trip.avoid}
    return 4 * len(terms & interests) - 5 * len(terms & avoid)


def _action_fingerprint(actions: list[RepairAction]) -> str:
    payload = "|".join(
        sorted(
            ";".join(
                [
                    action.kind.value,
                    action.source_violation_type,
                    action.poi_id,
                    action.from_day.isoformat() if action.from_day else "",
                    action.to_day.isoformat() if action.to_day else "",
                ]
            )
            for action in actions
        )
    )
    return sha256(payload.encode("utf-8")).hexdigest()[:16]


def _candidate_items(candidate: PlanCandidate) -> list[tuple[date, PlanItem]]:
    return [
        (day.date, item)
        for day in candidate.days
        for item in day.items
        if item.type is ItemType.ACTIVITY and item.poi_id is not None
    ]


def _available_minutes(trip: TripSpec, day: date) -> int:
    timezone = trip.arrival.at.tzinfo
    start = datetime.combine(day, trip.daily_start, tzinfo=timezone)
    end = datetime.combine(day, trip.daily_end, tzinfo=timezone)
    if day == trip.arrival.at.date():
        start = max(start, trip.arrival.at + timedelta(minutes=60))
    if day == trip.departure.at.date():
        end = min(end, trip.departure.at - timedelta(minutes=90))
    return max(0, round((end - start).total_seconds() / 60))


def _best_target_day(
    trip: TripSpec,
    draft: CandidateDraft,
    poi: PlanningPOI,
    *,
    excluded_day: date | None = None,
) -> date | None:
    candidates = []
    for draft_day in draft.days:
        if draft_day.date == excluded_day:
            continue
        opening = poi.opening_windows.get(draft_day.date)
        if opening is None:
            continue
        available = _available_minutes(trip, draft_day.date)
        opening_minutes = (
            datetime.combine(draft_day.date, opening.end)
            - datetime.combine(draft_day.date, opening.start)
        ).total_seconds() / 60
        if min(available, round(opening_minutes)) < poi.duration_minutes:
            continue
        candidates.append(
            (len(draft_day.poi_ids), -available, draft_day.date)
        )
    return min(candidates)[2] if candidates else None


def _deduplicate_actions(actions: list[RepairAction]) -> list[RepairAction]:
    seen: set[tuple[RepairActionKind, str, date | None, date | None]] = set()
    unique: list[RepairAction] = []
    for action in actions:
        key = (action.kind, action.poi_id, action.from_day, action.to_day)
        if key in seen:
            continue
        seen.add(key)
        unique.append(action)
    return unique


def _remove_actions_for_budget(
    trip: TripSpec,
    candidate: PlanCandidate,
    poi_by_id: dict[str, PlanningPOI],
) -> list[RepairAction]:
    assert trip.total_budget is not None
    excess = candidate.metrics.known_estimated_cost - trip.total_budget
    if excess <= 0:
        return []
    options = [
        (day, item, poi_by_id[item.poi_id])
        for day, item in _candidate_items(candidate)
        if item.poi_id in poi_by_id
        and item.estimated_cost is not None
        and item.estimated_cost > 0
        and not _is_required_name(item.name, trip)
    ]
    options.sort(
        key=lambda row: (
            -row[1].estimated_cost,
            _preference_score(row[2], trip),
            row[1].poi_id,
        )
    )
    saved = Decimal("0")
    actions: list[RepairAction] = []
    for day, item, _ in options:
        assert item.poi_id is not None and item.estimated_cost is not None
        actions.append(
            RepairAction(
                kind=RepairActionKind.REMOVE_OPTIONAL_POI,
                source_violation_type="budget_exceeded",
                poi_id=item.poi_id,
                from_day=day,
                reason="移除高费用且非必去的活动",
                expected_effect=f"减少已知费用 {item.estimated_cost} 元",
            )
        )
        saved += item.estimated_cost
        if saved >= excess:
            return actions
    return []


def _remove_actions_for_day_limit(
    trip: TripSpec,
    candidate: PlanCandidate,
    poi_by_id: dict[str, PlanningPOI],
    *,
    day: date,
    violation_type: str,
) -> list[RepairAction]:
    plan_day = next((item for item in candidate.days if item.date == day), None)
    if plan_day is None:
        return []
    if violation_type == "walking_limit":
        excess = plan_day.walking_distance_meters - trip.mobility.max_daily_walking_meters
        contribution = lambda item: min(
            round(item.distance_from_previous_meters * 0.12), 2_000
        )
    else:
        activity_minutes = sum(
            round((item.end_at - item.start_at).total_seconds() / 60)
            for item in plan_day.items
            if item.type is ItemType.ACTIVITY
        )
        excess = activity_minutes - trip.mobility.max_daily_activity_minutes
        contribution = lambda item: round(
            (item.end_at - item.start_at).total_seconds() / 60
        )
    if excess <= 0:
        return []
    options = [
        item
        for item in plan_day.items
        if item.type is ItemType.ACTIVITY
        and item.poi_id in poi_by_id
        and not _is_required_name(item.name, trip)
    ]
    options.sort(
        key=lambda item: (
            -contribution(item),
            _preference_score(poi_by_id[item.poi_id], trip),
            item.poi_id,
        )
    )
    saved = 0
    actions: list[RepairAction] = []
    for item in options:
        assert item.poi_id is not None
        amount = contribution(item)
        actions.append(
            RepairAction(
                kind=RepairActionKind.REMOVE_OPTIONAL_POI,
                source_violation_type=violation_type,
                poi_id=item.poi_id,
                from_day=day,
                reason="降低受影响日期的活动密度",
                expected_effect=f"降低 {violation_type} 约 {amount}",
            )
        )
        saved += amount
        if saved >= excess:
            return actions
    return []


def _actions_for_missing_must_visit(
    trip: TripSpec,
    candidate: PlanCandidate,
    draft: CandidateDraft,
    pois: list[PlanningPOI],
) -> list[RepairAction]:
    scheduled_names = {
        _normalize(item.name)
        for day in candidate.days
        for item in day.items
    }
    actions: list[RepairAction] = []
    for required in trip.must_visit:
        normalized = _normalize(required)
        if any(
            normalized in scheduled or scheduled in normalized
            for scheduled in scheduled_names
        ):
            continue
        poi = next(
            (
                item
                for item in pois
                if normalized in _normalize(item.facts.name)
                or _normalize(item.facts.name) in normalized
            ),
            None,
        )
        if poi is None:
            return []
        current_day = next(
            (
                day.date
                for day in draft.days
                if poi.facts.id in day.poi_ids
            ),
            None,
        )
        target_day = _best_target_day(
            trip,
            draft,
            poi,
            excluded_day=current_day,
        )
        if target_day is None:
            target_day = _best_target_day(trip, draft, poi)
        if target_day is None:
            return []
        kind = (
            RepairActionKind.MOVE_POI
            if current_day is not None and current_day != target_day
            else RepairActionKind.INSERT_MUST_VISIT
        )
        actions.append(
            RepairAction(
                kind=kind,
                source_violation_type="missing_must_visit",
                poi_id=poi.facts.id,
                from_day=current_day,
                to_day=target_day,
                reason="把遗漏的必去地点放入可用时间更充足的日期",
                expected_effect=f"覆盖必去地点 {required}",
            )
        )
    return actions


def _actions_for_empty_plan(
    trip: TripSpec,
    draft: CandidateDraft,
    pois: list[PlanningPOI],
) -> list[RepairAction]:
    if not pois:
        return []
    ranked = sorted(
        pois,
        key=lambda poi: (
            0 if _is_required_poi(poi, trip) else 1,
            -_preference_score(poi, trip),
            -poi.data_confidence,
            poi.facts.id,
        ),
    )
    for poi in ranked:
        target = _best_target_day(trip, draft, poi)
        if target is not None:
            return [
                RepairAction(
                    kind=RepairActionKind.ADD_AVAILABLE_POI,
                    source_violation_type="empty_plan",
                    poi_id=poi.facts.id,
                    to_day=target,
                    reason="为空计划补入可执行地点",
                    expected_effect="计划至少包含一个活动",
                )
            ]
    return []


def _actions_for_local_time_violation(
    trip: TripSpec,
    candidate: PlanCandidate,
    draft: CandidateDraft,
    pois: list[PlanningPOI],
    *,
    violation_type: str,
    day: date | None,
    entity_ids: list[str],
) -> list[RepairAction]:
    if day is None:
        return []
    poi_by_id = {poi.facts.id: poi for poi in pois}
    plan_day = next((item for item in candidate.days if item.date == day), None)
    if plan_day is None:
        return []
    candidates = [
        item
        for item in plan_day.items
        if item.type is ItemType.ACTIVITY
        and item.poi_id is not None
        and (not entity_ids or item.poi_id in entity_ids)
    ]
    candidates.sort(key=lambda item: (item.start_at, item.poi_id), reverse=True)
    for item in candidates:
        assert item.poi_id is not None
        if not _is_required_name(item.name, trip):
            return [
                RepairAction(
                    kind=RepairActionKind.REMOVE_OPTIONAL_POI,
                    source_violation_type=violation_type,
                    poi_id=item.poi_id,
                    from_day=day,
                    reason="移除造成时间冲突的非必去活动",
                    expected_effect=f"修复 {violation_type}",
                )
            ]
        poi = poi_by_id.get(item.poi_id)
        if poi is None:
            continue
        target = _best_target_day(trip, draft, poi, excluded_day=day)
        if target is not None:
            return [
                RepairAction(
                    kind=RepairActionKind.MOVE_POI,
                    source_violation_type=violation_type,
                    poi_id=item.poi_id,
                    from_day=day,
                    to_day=target,
                    reason="把必去活动移动到可用日期",
                    expected_effect=f"修复 {violation_type} 且保留必去项",
                )
            ]
    return []


def build_repair_plan(
    trip: TripSpec,
    candidate: PlanCandidate,
    draft: CandidateDraft,
    pois: list[PlanningPOI],
    report: CriticReport,
    *,
    repair_round: int,
) -> tuple[RepairPlan | None, str | None]:
    if not report.repairable:
        return None, report.terminal_reason
    poi_by_id = {poi.facts.id: poi for poi in pois}
    actions: list[RepairAction] = []
    errors = [
        violation
        for violation in candidate.validation.violations
        if violation.severity.value == "error"
    ] if candidate.validation else []
    for violation in errors:
        if violation.type == "budget_exceeded":
            generated = _remove_actions_for_budget(trip, candidate, poi_by_id)
        elif violation.type in {"walking_limit", "activity_time_limit"}:
            generated = (
                _remove_actions_for_day_limit(
                    trip,
                    candidate,
                    poi_by_id,
                    day=violation.day,
                    violation_type=violation.type,
                )
                if violation.day is not None
                else []
            )
        elif violation.type == "missing_must_visit":
            generated = _actions_for_missing_must_visit(
                trip, candidate, draft, pois
            )
        elif violation.type == "empty_plan":
            generated = _actions_for_empty_plan(trip, draft, pois)
        else:
            generated = _actions_for_local_time_violation(
                trip,
                candidate,
                draft,
                pois,
                violation_type=violation.type,
                day=violation.day,
                entity_ids=violation.entity_ids,
            )
        if not generated:
            return None, f"no_safe_repair_action:{violation.type}"
        actions.extend(generated)

    actions = _deduplicate_actions(actions)
    if not actions:
        return None, "empty_repair_plan"
    affected_days = tuple(
        sorted(
            {
                day
                for action in actions
                for day in (action.from_day, action.to_day)
                if day is not None
            }
        )
    )
    preserved_days = tuple(
        day.date for day in draft.days if day.date not in affected_days
    )
    return (
        RepairPlan(
            round=repair_round,
            target_candidate_id=candidate.id,
            source_violation_types=tuple(
                sorted({action.source_violation_type for action in actions})
            ),
            actions=tuple(actions),
            affected_days=affected_days,
            preserved_days=preserved_days,
            expected_effects=tuple(action.expected_effect for action in actions),
            action_fingerprint=_action_fingerprint(actions),
        ),
        None,
    )


def apply_repair_plan(
    trip: TripSpec,
    draft: CandidateDraft,
    pois: list[PlanningPOI],
    plan: RepairPlan,
    *,
    route_strategy: int,
    route_mode: RouteMode = RouteMode.DRIVING,
    route_modes: tuple[RouteMode, ...] | None = None,
    max_walking_leg_meters: int = 1_500,
) -> tuple[CandidateDraft, RepairPlan]:
    day_pois = {day.date: list(day.poi_ids) for day in draft.days}
    for action in plan.actions:
        if action.kind is RepairActionKind.REMOVE_OPTIONAL_POI:
            if action.from_day is not None:
                day_pois[action.from_day] = [
                    poi_id
                    for poi_id in day_pois[action.from_day]
                    if poi_id != action.poi_id
                ]
            continue

        for day in day_pois:
            day_pois[day] = [
                poi_id for poi_id in day_pois[day] if poi_id != action.poi_id
            ]
        if action.to_day is not None:
            day_pois[action.to_day].insert(0, action.poi_id)

    repaired = CandidateDraft(
        id=f"{draft.id}-repair-r{plan.round}",
        style=draft.style,
        days=tuple(
            DraftDay(date=day.date, poi_ids=tuple(day_pois[day.date]))
            for day in draft.days
        ),
    )
    if repaired.days == draft.days:
        raise ValueError("repair plan did not change candidate draft")
    invalidated = invalidated_route_keys(
        trip,
        draft,
        repaired,
        pois,
        route_strategy=route_strategy,
        route_mode=route_mode,
        route_modes=route_modes,
        max_walking_leg_meters=max_walking_leg_meters,
    )
    return repaired, plan.model_copy(update={"invalidated_route_keys": invalidated})
