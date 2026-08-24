from __future__ import annotations

from hashlib import sha256
import json

from travel_agent.domain.models import DayPlan, PlanCandidate, PlanItem


def _hash(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def stable_item_id(
    session_id: str,
    candidate_id: str,
    item: PlanItem,
    day: object,
    occurrence: int,
) -> str:
    identity = (
        session_id,
        candidate_id,
        item.poi_id or item.name.strip().casefold(),
        str(day),
        occurrence,
    )
    return f"item_{_hash(identity)[:20]}"


def new_item_id(session_id: str, request_id: str, operation_index: int) -> str:
    return f"item_{_hash((session_id, request_id, operation_index))[:20]}"


def with_stable_item_ids(session_id: str, candidate: PlanCandidate) -> PlanCandidate:
    occurrences: dict[tuple[str, str], int] = {}
    days = []
    for day in candidate.days:
        items = []
        for item in day.items:
            key = (str(day.date), item.poi_id or item.name.strip().casefold())
            occurrence = occurrences.get(key, 0)
            occurrences[key] = occurrence + 1
            item_id = item.item_id or stable_item_id(
                session_id, candidate.id, item, day.date, occurrence
            )
            items.append(item.model_copy(update={"item_id": item_id, "locked": False}))
        days.append(day.model_copy(update={"items": items}))
    return candidate.model_copy(update={"days": days})


def item_fingerprint(item: PlanItem, day: object) -> str:
    return _hash(
        {
            "item_id": item.item_id,
            "poi_id": item.poi_id,
            "name": item.name,
            "day": str(day),
            "start": item.start_at.isoformat(),
            "end": item.end_at.isoformat(),
            "type": item.type.value,
        }
    )


def day_fingerprint(day: DayPlan) -> str:
    return _hash(
        {
            "date": day.date.isoformat(),
            "theme": day.theme,
            "area": day.primary_area,
            "items": [
                {
                    "id": item.item_id,
                    "poi": item.poi_id,
                    "name": item.name,
                    "start": item.start_at.isoformat(),
                    "end": item.end_at.isoformat(),
                    "travel": item.travel_from_previous_minutes,
                    "distance": item.distance_from_previous_meters,
                }
                for item in day.items
            ],
            "cost": str(day.known_estimated_cost),
            "travel": day.total_travel_minutes,
            "walking": day.walking_distance_meters,
        }
    )


def plan_fingerprint(candidate: PlanCandidate) -> str:
    return _hash([day_fingerprint(day) for day in candidate.days])

