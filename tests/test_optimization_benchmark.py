from pathlib import Path

import pytest

from travel_agent.domain.optimization_models import OptimizationBudget
from travel_agent.domain.tool_models import UnknownFactPolicy
from travel_agent.graph.workflow import build_workflow
from travel_agent.planning.defaults import POIDefaultPolicy
from travel_agent.planning.optimization import OptimizationTimeoutError
from travel_agent.planning.optimization_evaluation import (
    evaluate_optimization_variants,
    load_optimization_cases,
    optimization_report_as_json,
)
from travel_agent.planning.policy import PlanningPolicy


DATASET = Path("evals/optimization/cases.jsonl")


class ForcedHeuristicOptimizer:
    name = "test-forced-heuristic"

    def solve(self, _problem):
        raise OptimizationTimeoutError("benchmark ablation")


def _workflow(
    gateway_factory,
    *,
    variants: int,
    real_walking: bool,
    heuristic: bool = False,
):
    return build_workflow(
        gateway_factory(),
        POIDefaultPolicy(UnknownFactPolicy.ASSUME_WITH_WARNING),
        PlanningPolicy(use_real_walking_routes=real_walking),
        optimizer=ForcedHeuristicOptimizer() if heuristic else None,
        optimization_budget=OptimizationBudget(variant_count=variants),
    )


def test_optimization_dataset_covers_constraint_dimensions():
    cases = load_optimization_cases(DATASET)

    assert len(cases) >= 4
    assert {case.category for case in cases} >= {
        "baseline",
        "budget",
        "time_window",
        "walking",
    }


@pytest.mark.asyncio
async def test_optimization_benchmark_reports_required_ablations(
    gateway_factory,
    hangzhou_trip,
):
    cases = load_optimization_cases(DATASET)
    workflows = {
        "optimizer-three-real": _workflow(
            gateway_factory,
            variants=3,
            real_walking=True,
        ),
        "optimizer-one-real": _workflow(
            gateway_factory,
            variants=1,
            real_walking=True,
        ),
        "heuristic-three-real": _workflow(
            gateway_factory,
            variants=3,
            real_walking=True,
            heuristic=True,
        ),
        "heuristic-three-estimated": _workflow(
            gateway_factory,
            variants=3,
            real_walking=False,
            heuristic=True,
        ),
    }

    report = await evaluate_optimization_variants(
        workflows,
        hangzhou_trip,
        cases,
    )
    by_name = {variant.name: variant for variant in report.variants}

    assert report.case_count == len(cases)
    assert set(by_name) == set(workflows)
    assert by_name["optimizer-three-real"].solve_success_rate == 1.0
    assert by_name["heuristic-three-real"].degraded_rate == 1.0
    assert by_name["optimizer-three-real"].average_candidate_count == 3.0
    assert by_name["optimizer-one-real"].average_candidate_count == 1.0
    assert by_name["optimizer-three-real"].grounded_walking_fact_rate == 1.0
    assert (
        by_name["heuristic-three-estimated"].grounded_walking_fact_rate < 1.0
    )
    assert all(
        variant.constraint_satisfaction_rate == 1.0
        for variant in report.variants
    )
    serialized = optimization_report_as_json(report)
    assert '"average_route_efficiency"' in serialized
    assert '"average_elapsed_ms"' in serialized
