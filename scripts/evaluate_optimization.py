from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from travel_agent.config import Settings
from travel_agent.domain.models import TripSpec
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
from travel_agent.tools.gateway import build_gateway
from travel_agent.tools.providers.mock import MockPOIProvider, MockRouteProvider


class ForcedHeuristicOptimizer:
    name = "benchmark-forced-heuristic"

    def solve(self, _problem):
        raise OptimizationTimeoutError("benchmark heuristic ablation")


def _workflow(
    *,
    variant_count: int,
    real_walking: bool,
    heuristic: bool,
):
    settings = Settings.from_env({})
    gateway = build_gateway(settings, MockPOIProvider(), MockRouteProvider())
    return build_workflow(
        gateway,
        POIDefaultPolicy(UnknownFactPolicy.ASSUME_WITH_WARNING),
        PlanningPolicy(use_real_walking_routes=real_walking),
        optimizer=ForcedHeuristicOptimizer() if heuristic else None,
        optimization_budget=OptimizationBudget(variant_count=variant_count),
    )


async def _run(dataset: Path, base_trip_path: Path) -> int:
    cases = load_optimization_cases(dataset)
    base_trip = TripSpec.model_validate_json(
        base_trip_path.read_text(encoding="utf-8")
    )
    workflows = {
        "optimizer-three-real": _workflow(
            variant_count=3,
            real_walking=True,
            heuristic=False,
        ),
        "optimizer-one-real": _workflow(
            variant_count=1,
            real_walking=True,
            heuristic=False,
        ),
        "heuristic-three-real": _workflow(
            variant_count=3,
            real_walking=True,
            heuristic=True,
        ),
        "heuristic-three-estimated": _workflow(
            variant_count=3,
            real_walking=False,
            heuristic=True,
        ),
    }
    report = await evaluate_optimization_variants(workflows, base_trip, cases)
    print(optimization_report_as_json(report))
    return int(
        any(
            variant.completed_count == 0
            or variant.constraint_satisfaction_rate < 1.0
            for variant in report.variants
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="评测约束优化与路线事实消融")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("evals/optimization/cases.jsonl"),
    )
    parser.add_argument(
        "--base-trip",
        type=Path,
        default=Path("evals/repairs/base_trip.json"),
    )
    args = parser.parse_args()
    return asyncio.run(_run(args.dataset, args.base_trip))


if __name__ == "__main__":
    raise SystemExit(main())
