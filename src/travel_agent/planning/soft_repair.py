from __future__ import annotations

from hashlib import sha256

from travel_agent.domain.critique_models import (
    SoftCritique,
    SoftRepairPlan,
    SuggestedActionKind,
)
from travel_agent.domain.models import PlanCandidate, PlanningPOI, TripSpec
from travel_agent.domain.repair_models import RepairAction, RepairActionKind
from travel_agent.planning.drafts import CandidateDraft, DraftDay


def _normalize(value: str) -> str:
    return value.strip().casefold()


def _is_must_visit(poi_name: str, trip: TripSpec) -> bool:
    normalized = _normalize(poi_name)
    return any(
        _normalize(required) in normalized or normalized in _normalize(required)
        for required in trip.must_visit
    )


def compile_soft_repair_plan(
    trip: TripSpec,
    candidate: PlanCandidate,
    draft: CandidateDraft,
    pois: list[PlanningPOI],
    critique: SoftCritique,
    *,
    repair_round: int,
) -> tuple[SoftRepairPlan | None, str | None]:
    """把 LLM Intent 编译成单个、受本地实体和必去保护约束的动作。"""
    if critique.candidate_id != candidate.id or draft.id != candidate.id:
        return None, "soft_context_mismatch"
    poi_by_id = {poi.facts.id: poi for poi in pois}
    day_ids = {day.date: list(day.poi_ids) for day in draft.days}
    total_pois = sum(len(ids) for ids in day_ids.values())
    ordered_dimensions = sorted(
        critique.dimensions,
        key=lambda item: (item.score, item.dimension.value),
    )
    for dimension in ordered_dimensions:
        suggestion = dimension.suggested_action
        if suggestion is None or suggestion.kind is SuggestedActionKind.NO_ACTION:
            continue
        if suggestion.poi_id is None or suggestion.from_day is None:
            continue
        poi = poi_by_id.get(suggestion.poi_id)
        source_ids = day_ids.get(suggestion.from_day)
        if poi is None or source_ids is None or suggestion.poi_id not in source_ids:
            continue
        if _is_must_visit(poi.facts.name, trip):
            continue
        if suggestion.kind is SuggestedActionKind.REMOVE_OPTIONAL_POI:
            if total_pois <= 1:
                continue
            interest_terms = {_normalize(item) for item in trip.interests}
            remaining_ids = {
                poi_id
                for ids in day_ids.values()
                for poi_id in ids
                if poi_id != suggestion.poi_id
            }
            if interest_terms and not any(
                interest_terms
                & {
                    _normalize(value)
                    for value in remaining.facts.categories
                }
                for poi_id in remaining_ids
                if (remaining := poi_by_id.get(poi_id)) is not None
            ):
                continue
            kind = RepairActionKind.REMOVE_OPTIONAL_POI
            to_day = None
        elif suggestion.kind is SuggestedActionKind.MOVE_OPTIONAL_POI:
            if suggestion.to_day not in day_ids:
                continue
            assert suggestion.to_day is not None
            if suggestion.to_day not in poi.opening_windows:
                continue
            target_minutes = sum(
                poi_by_id[poi_id].duration_minutes
                for poi_id in day_ids[suggestion.to_day]
                if poi_id in poi_by_id
            )
            if (
                target_minutes + poi.duration_minutes
                > trip.mobility.max_daily_activity_minutes
            ):
                continue
            kind = RepairActionKind.MOVE_POI
            to_day = suggestion.to_day
        else:
            if len(source_ids) < 2:
                continue
            kind = RepairActionKind.REORDER_OPTIONAL_POI
            to_day = suggestion.from_day
        action = RepairAction(
            kind=kind,
            source_violation_type=f"soft:{dimension.dimension.value}",
            poi_id=suggestion.poi_id,
            from_day=suggestion.from_day,
            to_day=to_day,
            reason=dimension.summary,
            expected_effect=f"改善 {dimension.dimension.value}",
        )
        affected = tuple(
            sorted({day for day in (suggestion.from_day, to_day) if day is not None})
        )
        preserved = tuple(day.date for day in draft.days if day.date not in affected)
        fingerprint_source = "|".join(
            (
                candidate.id,
                kind.value,
                suggestion.poi_id,
                str(suggestion.from_day),
                str(to_day or ""),
            )
        )
        return (
            SoftRepairPlan(
                round=repair_round,
                target_candidate_id=candidate.id,
                source_dimension=dimension.dimension,
                source_evidence_ids=suggestion.evidence_ids,
                action=action,
                affected_days=affected,
                preserved_days=preserved,
                action_fingerprint=sha256(
                    fingerprint_source.encode("utf-8")
                ).hexdigest()[:24],
            ),
            None,
        )
    return None, "no_safe_soft_action"


def apply_soft_repair(
    draft: CandidateDraft,
    plan: SoftRepairPlan,
) -> CandidateDraft:
    day_pois = {day.date: list(day.poi_ids) for day in draft.days}
    action = plan.action
    source_day = action.from_day
    if source_day is None or action.poi_id not in day_pois.get(source_day, []):
        raise ValueError("soft repair source POI is not scheduled on from_day")
    if action.kind is RepairActionKind.REMOVE_OPTIONAL_POI:
        day_pois[source_day].remove(action.poi_id)
    elif action.kind is RepairActionKind.MOVE_POI:
        if action.to_day is None or action.to_day not in day_pois:
            raise ValueError("soft move requires a valid target day")
        day_pois[source_day].remove(action.poi_id)
        day_pois[action.to_day].append(action.poi_id)
    elif action.kind is RepairActionKind.REORDER_OPTIONAL_POI:
        ids = day_pois[source_day]
        ids.remove(action.poi_id)
        ids.insert(0, action.poi_id)
    else:
        raise ValueError("unsupported soft repair action")
    return CandidateDraft(
        id=f"{draft.id}-soft-r{plan.round}",
        style=draft.style,
        days=tuple(
            DraftDay(date=day.date, poi_ids=tuple(day_pois[day.date]))
            for day in draft.days
        ),
    )
