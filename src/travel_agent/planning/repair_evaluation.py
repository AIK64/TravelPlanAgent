from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from time import perf_counter
from typing import Any

from pydantic import BaseModel, Field

from travel_agent.domain.models import (
    PlanningRequest,
    TripSpec,
    ValidationStatus,
)
from travel_agent.domain.repair_models import RepairActionKind
from travel_agent.graph.workflow import run_planning


class RepairBenchmarkCase(BaseModel):
    id: str
    category: str
    trip_patch: dict[str, Any] = Field(default_factory=dict)
    max_replan_rounds: int = Field(default=2, ge=0, le=5)
    expected_status: str
    expected_rounds: int = Field(ge=0)
    expected_action_kinds: list[RepairActionKind] = Field(default_factory=list)
    expected_terminal_reason: str | None = None
    minimum_preserved_days: int = Field(default=0, ge=0)


class RepairCaseResult(BaseModel):
    id: str
    category: str
    exact: bool
    actual_status: str | None = None
    actual_rounds: int = Field(default=0, ge=0)
    actual_action_kinds: list[RepairActionKind] = Field(default_factory=list)
    terminal_reason: str | None = None
    hard_constraints_satisfied: bool
    bounded_termination: bool
    locality: float = Field(ge=0, le=1)
    route_reuse_rate: float = Field(ge=0, le=1)
    elapsed_ms: float = Field(ge=0)
    error_type: str | None = None


class RepairBenchmarkReport(BaseModel):
    case_count: int
    execution_failure_count: int
    exact_case_accuracy: float
    repair_success_rate: float
    hard_constraint_satisfaction_rate: float
    bounded_termination_rate: float
    replanning_locality: float
    route_reuse_rate: float
    average_repair_rounds: float
    average_elapsed_ms: float
    cases: list[RepairCaseResult]


def repair_report_as_json(report: RepairBenchmarkReport) -> str:
    return json.dumps(
        report.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
    )


def load_repair_cases(path: Path) -> list[RepairBenchmarkCase]:
    cases: list[RepairBenchmarkCase] = []
    with path.open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                cases.append(RepairBenchmarkCase.model_validate_json(line))
            except Exception as error:
                raise ValueError(
                    f"invalid repair benchmark case at line {line_number}"
                ) from error
    if not cases:
        raise ValueError("repair benchmark dataset must not be empty")
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("repair benchmark case ids must be unique")
    return cases


def _patched_trip(base: TripSpec, patch: dict[str, Any]) -> TripSpec:
    payload = base.model_dump(mode="python")
    for field, value in patch.items():
        if field == "mobility":
            payload[field] = {
                **base.mobility.model_dump(mode="python"),
                **value,
            }
        else:
            payload[field] = value
    return TripSpec.model_validate(payload)


async def evaluate_repair_cases(
    workflow: object,
    base_trip: TripSpec,
    cases: Sequence[RepairBenchmarkCase],
) -> RepairBenchmarkReport:
    if not cases:
        raise ValueError("cases must not be empty")

    results: list[RepairCaseResult] = []
    repair_expected = 0
    repair_succeeded = 0
    completed_count = 0
    hard_valid_count = 0
    locality_numerator = 0
    locality_denominator = 0
    reused_routes = 0
    repair_routes = 0
    total_rounds = 0
    total_elapsed_ms = 0.0
    failures = 0

    for index, case in enumerate(cases):
        started = perf_counter()
        trip = _patched_trip(base_trip, case.trip_patch)
        actual_status: str | None = None
        actual_rounds = 0
        action_kinds: list[RepairActionKind] = []
        terminal_reason: str | None = None
        hard_satisfied = False
        bounded = False
        locality = 1.0
        route_reuse_rate = 1.0
        error_type: str | None = None
        attempts = []
        try:
            response = await run_planning(
                workflow,
                PlanningRequest(
                    trip=trip,
                    max_replan_rounds=case.max_replan_rounds,
                ),
                thread_id=f"repair-benchmark-{index}-{case.id}",
            )
            snapshot = await workflow.aget_state(
                {
                    "configurable": {
                        "thread_id": f"repair-benchmark-{index}-{case.id}"
                    }
                }
            )
            state = snapshot.values
            actual_status = response.status
            actual_rounds = response.iterations
            terminal_reason = state["repair_terminal_reason"]
            attempts = state["repair_history"]
            action_kinds = [
                kind for attempt in attempts for kind in attempt.action_kinds
            ]
            hard_satisfied = (
                response.status != "completed"
                or (
                    response.selected_plan is not None
                    and response.selected_plan.validation is not None
                    and response.selected_plan.validation.status
                    is not ValidationStatus.INVALID
                )
            )
            bounded = (
                response.iterations <= case.max_replan_rounds
                and state["pending_replan_round"] is None
            )
            possible_preserved = sum(
                max(0, trip.day_count - len(attempt.affected_days))
                for attempt in attempts
            )
            actual_preserved = sum(
                attempt.preserved_day_count for attempt in attempts
            )
            locality = (
                actual_preserved / possible_preserved
                if possible_preserved
                else 1.0
            )
            reused = sum(attempt.reused_route_count for attempt in attempts)
            loaded = sum(attempt.loaded_route_count for attempt in attempts)
            route_reuse_rate = reused / (reused + loaded) if reused + loaded else 1.0
            locality_numerator += actual_preserved
            locality_denominator += possible_preserved
            reused_routes += reused
            repair_routes += reused + loaded
        except Exception as error:  # benchmark reports workflow failures
            failures += 1
            error_type = type(error).__name__

        expected_actions = sorted(kind.value for kind in case.expected_action_kinds)
        actual_actions = sorted(kind.value for kind in action_kinds)
        minimum_preserved = max(
            (attempt.preserved_day_count for attempt in attempts),
            default=0,
        ) if error_type is None else 0
        exact = (
            error_type is None
            and actual_status == case.expected_status
            and actual_rounds == case.expected_rounds
            and actual_actions == expected_actions
            and terminal_reason == case.expected_terminal_reason
            and minimum_preserved >= case.minimum_preserved_days
            and hard_satisfied
            and bounded
        )
        elapsed_ms = round((perf_counter() - started) * 1000, 2)
        total_elapsed_ms += elapsed_ms
        total_rounds += actual_rounds
        completed_count += int(actual_status == "completed")
        hard_valid_count += int(actual_status == "completed" and hard_satisfied)
        if case.expected_rounds > 0:
            repair_expected += 1
            repair_succeeded += int(actual_status == "completed" and exact)
        results.append(
            RepairCaseResult(
                id=case.id,
                category=case.category,
                exact=exact,
                actual_status=actual_status,
                actual_rounds=actual_rounds,
                actual_action_kinds=action_kinds,
                terminal_reason=terminal_reason,
                hard_constraints_satisfied=hard_satisfied,
                bounded_termination=bounded,
                locality=round(locality, 4),
                route_reuse_rate=round(route_reuse_rate, 4),
                elapsed_ms=elapsed_ms,
                error_type=error_type,
            )
        )

    return RepairBenchmarkReport(
        case_count=len(cases),
        execution_failure_count=failures,
        exact_case_accuracy=sum(result.exact for result in results) / len(results),
        repair_success_rate=(
            repair_succeeded / repair_expected if repair_expected else 1.0
        ),
        hard_constraint_satisfaction_rate=(
            hard_valid_count / completed_count if completed_count else 1.0
        ),
        bounded_termination_rate=(
            sum(result.bounded_termination for result in results) / len(results)
        ),
        replanning_locality=(
            locality_numerator / locality_denominator
            if locality_denominator
            else 1.0
        ),
        route_reuse_rate=(
            reused_routes / repair_routes if repair_routes else 1.0
        ),
        average_repair_rounds=round(total_rounds / len(results), 3),
        average_elapsed_ms=round(total_elapsed_ms / len(results), 2),
        cases=results,
    )
