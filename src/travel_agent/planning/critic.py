from __future__ import annotations

from hashlib import sha256

from travel_agent.domain.models import (
    PlanCandidate,
    PlanningPOI,
    TripSpec,
    Violation,
    ViolationSeverity,
)
from travel_agent.domain.repair_models import CriticReport


SUPPORTED_REPAIR_VIOLATIONS = frozenset(
    {
        "activity_time_limit",
        "arrival_buffer",
        "budget_exceeded",
        "departure_buffer",
        "empty_plan",
        "missing_must_visit",
        "outside_daily_window",
        "outside_opening_hours",
        "time_overlap",
        "walking_limit",
    }
)


def _normalize(value: str) -> str:
    return value.strip().casefold()


def _matches_required(name: str, trip: TripSpec) -> bool:
    normalized = _normalize(name)
    return any(
        _normalize(required) in normalized or normalized in _normalize(required)
        for required in trip.must_visit
    )


def error_violations(candidate: PlanCandidate) -> list[Violation]:
    if candidate.validation is None:
        return []
    return [
        violation
        for violation in candidate.validation.violations
        if violation.severity is ViolationSeverity.ERROR
    ]


def violation_fingerprint(candidate: PlanCandidate) -> str:
    violations = candidate.validation.violations if candidate.validation else []
    parts = sorted(
        (
            violation.type,
            violation.severity.value,
            violation.day.isoformat() if violation.day else "",
            ",".join(sorted(violation.entity_ids)),
        )
        for violation in violations
    )
    payload = "|".join(";".join(part) for part in parts)
    return sha256(payload.encode("utf-8")).hexdigest()[:16]


def _required_pois_available(
    candidate: PlanCandidate,
    trip: TripSpec,
    pois: list[PlanningPOI],
) -> bool:
    scheduled_names = {
        _normalize(item.name)
        for day in candidate.days
        for item in day.items
    }
    available_names = [_normalize(poi.facts.name) for poi in pois]
    for required in trip.must_visit:
        normalized = _normalize(required)
        if any(
            normalized in scheduled or scheduled in normalized
            for scheduled in scheduled_names
        ):
            continue
        if not any(
            normalized in available or available in normalized
            for available in available_names
        ):
            return False
    return True


def _budget_can_be_repaired(candidate: PlanCandidate, trip: TripSpec) -> bool:
    if trip.total_budget is None:
        return False
    required_cost = sum(
        (
            item.estimated_cost
            for day in candidate.days
            for item in day.items
            if item.estimated_cost is not None
            and _matches_required(item.name, trip)
        ),
        start=trip.total_budget * 0,
    )
    optional_cost = sum(
        (
            item.estimated_cost
            for day in candidate.days
            for item in day.items
            if item.estimated_cost is not None
            and not _matches_required(item.name, trip)
        ),
        start=trip.total_budget * 0,
    )
    excess = candidate.metrics.known_estimated_cost - trip.total_budget
    return required_cost <= trip.total_budget and optional_cost >= excess > 0


def analyze_candidate(
    candidate: PlanCandidate,
    trip: TripSpec,
    pois: list[PlanningPOI],
) -> CriticReport:
    violations = candidate.validation.violations if candidate.validation else []
    errors = [
        violation
        for violation in violations
        if violation.severity is ViolationSeverity.ERROR
    ]
    warnings = [
        violation
        for violation in violations
        if violation.severity is ViolationSeverity.WARNING
    ]
    violation_types = tuple(sorted({violation.type for violation in errors}))
    affected_days = tuple(
        sorted({violation.day for violation in errors if violation.day is not None})
    )
    affected_poi_ids = tuple(
        sorted(
            {
                poi_id
                for violation in errors
                for poi_id in violation.entity_ids
            }
        )
    )

    terminal_reason: str | None = None
    unsupported = sorted(set(violation_types) - SUPPORTED_REPAIR_VIOLATIONS)
    if not errors:
        terminal_reason = "no_error_violation"
    elif unsupported:
        terminal_reason = f"unsupported_violation:{','.join(unsupported)}"
    elif "budget_exceeded" in violation_types and not _budget_can_be_repaired(
        candidate, trip
    ):
        terminal_reason = "hard_constraint_conflict:budget"
    elif "missing_must_visit" in violation_types and not _required_pois_available(
        candidate, trip, pois
    ):
        terminal_reason = "missing_required_poi_facts"
    elif "empty_plan" in violation_types and not pois:
        terminal_reason = "no_available_poi"

    return CriticReport(
        candidate_id=candidate.id,
        violation_fingerprint=violation_fingerprint(candidate),
        error_count=len(errors),
        warning_count=len(warnings),
        violation_types=violation_types,
        affected_days=affected_days,
        affected_poi_ids=affected_poi_ids,
        repairable=terminal_reason is None,
        terminal_reason=terminal_reason,
    )


def select_repair_target(
    candidates: list[PlanCandidate],
    trip: TripSpec,
    pois: list[PlanningPOI],
) -> PlanCandidate:
    if not candidates:
        raise ValueError("repair target requires at least one candidate")
    reports = {
        candidate.id: analyze_candidate(candidate, trip, pois)
        for candidate in candidates
    }
    return min(
        candidates,
        key=lambda candidate: (
            0 if reports[candidate.id].repairable else 1,
            reports[candidate.id].error_count,
            reports[candidate.id].warning_count,
            -(candidate.score if candidate.score is not None else float("-inf")),
            candidate.id,
        ),
    )
