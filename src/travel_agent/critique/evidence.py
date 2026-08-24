from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json

from travel_agent.domain.critique_models import (
    CandidateEvidenceDigest,
    EvidenceItem,
    EvidenceKind,
)
from travel_agent.domain.models import PlanCandidate, PlanningPOI, TripSpec


@dataclass(frozen=True, slots=True)
class EvidenceBudget:
    max_candidates: int = 3
    max_items_per_candidate: int = 48
    max_input_chars: int = 24_000

    def __post_init__(self) -> None:
        if self.max_candidates < 1 or self.max_items_per_candidate < 1:
            raise ValueError("evidence count budgets must be positive")
        if self.max_input_chars < 1:
            raise ValueError("max_input_chars must be positive")


def _evidence_id(
    candidate_id: str,
    kind: EvidenceKind,
    day: object,
    entity_id: str | None,
    field: str,
) -> str:
    identity = "|".join(
        (candidate_id, kind.value, str(day or ""), entity_id or "", field)
    )
    return f"ev_{sha256(identity.encode('utf-8')).hexdigest()[:20]}"


def _item(
    candidate_id: str,
    kind: EvidenceKind,
    field: str,
    value: str | int | float | bool,
    *,
    day=None,
    entity_id: str | None = None,
    source: str = "normalized",
    confidence: float = 1.0,
) -> EvidenceItem:
    return EvidenceItem(
        id=_evidence_id(candidate_id, kind, day, entity_id, field),
        kind=kind,
        candidate_id=candidate_id,
        day=day,
        entity_id=entity_id,
        field=field,
        value=value,
        source=source,
        confidence=confidence,
    )


def _candidate_items(
    trip: TripSpec,
    candidate: PlanCandidate,
    pois: list[PlanningPOI],
) -> list[EvidenceItem]:
    cid = candidate.id
    items: list[EvidenceItem] = []
    preferences = {
        "pace": trip.pace.value,
        "interests": ",".join(trip.interests) or "none",
        "avoid": ",".join(trip.avoid) or "none",
        "must_visit": ",".join(trip.must_visit) or "none",
        "needs_frequent_rest": trip.mobility.needs_frequent_rest,
        "max_daily_activity_minutes": trip.mobility.max_daily_activity_minutes,
        "max_daily_walking_meters": trip.mobility.max_daily_walking_meters,
    }
    for field, value in preferences.items():
        items.append(_item(cid, EvidenceKind.TRIP_PREFERENCE, field, value))

    metrics = candidate.metrics
    for field, value in {
        "preference_match": metrics.preference_match,
        "diversity": metrics.diversity,
        "fatigue_score": metrics.fatigue_score,
        "data_confidence": metrics.data_confidence,
        "total_travel_minutes": metrics.total_travel_minutes,
        "walking_distance_meters": metrics.walking_distance_meters,
        "known_estimated_cost": float(metrics.known_estimated_cost),
        "unknown_cost_item_count": metrics.unknown_cost_item_count,
    }.items():
        items.append(_item(cid, EvidenceKind.CANDIDATE_METRIC, field, value))

    for day in candidate.days:
        activity_minutes = sum(
            int((plan_item.end_at - plan_item.start_at).total_seconds() // 60)
            for plan_item in day.items
        )
        if day.items:
            occupied = int(
                (day.items[-1].end_at - day.items[0].start_at).total_seconds() // 60
            )
        else:
            occupied = 0
        day_values = {
            "activity_count": len(day.items),
            "activity_minutes": activity_minutes,
            "occupied_span_minutes": occupied,
            "total_travel_minutes": day.total_travel_minutes,
            "walking_distance_meters": day.walking_distance_meters,
            "fatigue_score": day.fatigue_score,
        }
        for field, value in day_values.items():
            items.append(
                _item(cid, EvidenceKind.DAY_METRIC, field, value, day=day.date)
            )

    poi_by_id = {poi.facts.id: poi for poi in pois}
    schedule_primary: list[EvidenceItem] = []
    poi_items: list[EvidenceItem] = []
    schedule_details: list[EvidenceItem] = []
    route_items: list[EvidenceItem] = []
    for day in candidate.days:
        for index, plan_item in enumerate(day.items):
            if plan_item.poi_id is None:
                continue
            poi = poi_by_id.get(plan_item.poi_id)
            is_must = any(
                required.strip().casefold() in plan_item.name.strip().casefold()
                or plan_item.name.strip().casefold() in required.strip().casefold()
                for required in trip.must_visit
            )
            for field, value in {
                "name": plan_item.name,
                "must_visit": is_must,
            }.items():
                schedule_primary.append(
                    _item(
                        cid,
                        EvidenceKind.SCHEDULE_FACT,
                        field,
                        value,
                        day=day.date,
                        entity_id=plan_item.poi_id,
                        source="candidate_schedule",
                    )
                )
            for field, value in {
                "position": index,
                "start_at": plan_item.start_at.isoformat(),
                "end_at": plan_item.end_at.isoformat(),
            }.items():
                schedule_details.append(
                    _item(
                        cid,
                        EvidenceKind.SCHEDULE_FACT,
                        field,
                        value,
                        day=day.date,
                        entity_id=plan_item.poi_id,
                        source="candidate_schedule",
                    )
                )
            for field, value in {
                "travel_minutes_from_previous": plan_item.travel_from_previous_minutes,
                "distance_meters_from_previous": plan_item.distance_from_previous_meters,
            }.items():
                route_items.append(
                    _item(
                        cid,
                        EvidenceKind.ROUTE_FACT,
                        field,
                        value,
                        day=day.date,
                        entity_id=plan_item.poi_id,
                        source="route_gateway",
                    )
                )
            if poi is not None:
                for field, value in {
                    "categories": ",".join(poi.facts.categories) or "unknown",
                    "duration_minutes": poi.duration_minutes,
                    "data_confidence": poi.data_confidence,
                }.items():
                    poi_items.append(
                        _item(
                            cid,
                            EvidenceKind.POI_FACT,
                            field,
                            value,
                            entity_id=poi.facts.id,
                            source=poi.facts.provider,
                            confidence=poi.data_confidence,
                        )
                    )
    assumption_items = []
    for assumption in candidate.assumptions:
        assumption_items.append(
            _item(
                cid,
                EvidenceKind.ASSUMPTION,
                assumption.field,
                assumption.value,
                source=assumption.source.value,
                confidence=0.5,
            )
        )
    # 裁剪优先级：偏好/指标 → 所有已安排实体 → POI 类别 → 时间线 → 路线 → 假设。
    return [
        *items,
        *schedule_primary,
        *poi_items,
        *schedule_details,
        *route_items,
        *assumption_items,
    ]


def build_evidence_digests(
    trip: TripSpec,
    candidates: list[PlanCandidate],
    pois: list[PlanningPOI],
    budget: EvidenceBudget = EvidenceBudget(),
) -> tuple[CandidateEvidenceDigest, ...]:
    """只把白名单事实写入有界 Digest；顺序就是确定性的裁剪优先级。"""
    digests: list[CandidateEvidenceDigest] = []
    remaining_chars = budget.max_input_chars
    for candidate in candidates[: budget.max_candidates]:
        all_items = _candidate_items(trip, candidate, pois)
        selected: list[EvidenceItem] = []
        truncated = len(all_items) > budget.max_items_per_candidate
        for item in all_items[: budget.max_items_per_candidate]:
            tentative = (*selected, item)
            chars = len(
                json.dumps(
                    [entry.model_dump(mode="json") for entry in tentative],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
            if chars > remaining_chars:
                truncated = True
                break
            selected.append(item)
        if not selected:
            break
        input_chars = len(
            json.dumps(
                [entry.model_dump(mode="json") for entry in selected],
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        remaining_chars -= input_chars
        digests.append(
            CandidateEvidenceDigest(
                candidate_id=candidate.id,
                style=candidate.style,
                evidence=tuple(selected),
                input_chars=input_chars,
                truncated=truncated,
            )
        )
    return tuple(digests)
