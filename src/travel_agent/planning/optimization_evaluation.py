from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from time import perf_counter
from typing import Any

from pydantic import BaseModel, Field

from travel_agent.domain.models import PlanningRequest, TripSpec, ValidationStatus
from travel_agent.domain.optimization_models import OptimizationSolveStatus
from travel_agent.graph.workflow import run_planning


class OptimizationBenchmarkCase(BaseModel):
    id: str
    category: str
    trip_patch: dict[str, Any] = Field(default_factory=dict)
    max_replan_rounds: int = Field(default=2, ge=0, le=5)


class OptimizationVariantReport(BaseModel):
    name: str
    case_count: int
    completed_count: int
    constraint_satisfaction_rate: float = Field(ge=0, le=1)
    solve_success_rate: float = Field(ge=0, le=1)
    degraded_rate: float = Field(ge=0, le=1)
    average_candidate_count: float = Field(ge=0)
    average_travel_minutes: float = Field(ge=0)
    average_route_efficiency: float = Field(ge=0)
    grounded_walking_fact_rate: float = Field(ge=0, le=1)
    average_elapsed_ms: float = Field(ge=0)


class OptimizationBenchmarkReport(BaseModel):
    case_count: int
    variants: list[OptimizationVariantReport]


def load_optimization_cases(path: Path) -> list[OptimizationBenchmarkCase]:
    cases: list[OptimizationBenchmarkCase] = []
    with path.open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                cases.append(OptimizationBenchmarkCase.model_validate_json(line))
            except Exception as error:
                raise ValueError(
                    f"invalid optimization benchmark case at line {line_number}"
                ) from error
    if not cases:
        raise ValueError("optimization benchmark cases must not be empty")
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("optimization benchmark case ids must be unique")
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


async def evaluate_optimization_variants(
    workflows: Mapping[str, object],
    base_trip: TripSpec,
    cases: Sequence[OptimizationBenchmarkCase],
) -> OptimizationBenchmarkReport:
    if not workflows:
        raise ValueError("workflows must not be empty")
    if not cases:
        raise ValueError("cases must not be empty")

    variants: list[OptimizationVariantReport] = []
    for variant_name, workflow in workflows.items():
        completed = 0
        constraints_satisfied = 0
        solve_successes = 0
        degraded = 0
        candidate_count = 0
        travel_minutes = 0
        route_efficiency = 0.0
        grounded_items = 0
        scheduled_items = 0
        elapsed_ms = 0.0
        for index, case in enumerate(cases):
            started = perf_counter()
            response = await run_planning(
                workflow,
                PlanningRequest(
                    trip=_patched_trip(base_trip, case.trip_patch),
                    max_replan_rounds=case.max_replan_rounds,
                ),
                thread_id=f"optimization-benchmark-{variant_name}-{index}",
            )
            elapsed_ms += (perf_counter() - started) * 1000
            snapshot = await workflow.aget_state(
                {
                    "configurable": {
                        "thread_id": f"optimization-benchmark-{variant_name}-{index}"
                    }
                }
            )
            result = snapshot.values["optimization_result"]
            solve_successes += int(
                result.status
                in {
                    OptimizationSolveStatus.OPTIMAL,
                    OptimizationSolveStatus.FEASIBLE,
                }
            )
            degraded += int(result.status is OptimizationSolveStatus.DEGRADED)
            candidate_count += len(response.candidates)
            if response.status != "completed" or response.selected_plan is None:
                continue
            completed += 1
            plan = response.selected_plan
            constraints_satisfied += int(
                plan.validation is not None
                and plan.validation.status is not ValidationStatus.INVALID
            )
            activities = sum(len(day.items) for day in plan.days)
            travel_minutes += plan.metrics.total_travel_minutes
            route_efficiency += activities / max(1, plan.metrics.total_travel_minutes)
            for day in plan.days:
                for item in day.items:
                    scheduled_items += 1
                    grounded_items += int(not item.walking_distance_estimated)

        case_count = len(cases)
        variants.append(
            OptimizationVariantReport(
                name=variant_name,
                case_count=case_count,
                completed_count=completed,
                constraint_satisfaction_rate=(
                    constraints_satisfied / completed if completed else 0.0
                ),
                solve_success_rate=solve_successes / case_count,
                degraded_rate=degraded / case_count,
                average_candidate_count=round(candidate_count / case_count, 3),
                average_travel_minutes=round(
                    travel_minutes / completed if completed else 0.0,
                    3,
                ),
                average_route_efficiency=round(
                    route_efficiency / completed if completed else 0.0,
                    6,
                ),
                grounded_walking_fact_rate=(
                    grounded_items / scheduled_items if scheduled_items else 0.0
                ),
                average_elapsed_ms=round(elapsed_ms / case_count, 2),
            )
        )
    return OptimizationBenchmarkReport(case_count=len(cases), variants=variants)


def optimization_report_as_json(report: OptimizationBenchmarkReport) -> str:
    return json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2)
