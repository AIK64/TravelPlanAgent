from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from travel_agent.config import Settings
from travel_agent.domain.models import TripSpec
from travel_agent.domain.tool_models import UnknownFactPolicy
from travel_agent.graph.workflow import build_workflow
from travel_agent.planning.defaults import POIDefaultPolicy
from travel_agent.planning.optimization import OptimizationTimeoutError
from travel_agent.planning.policy import PlanningPolicy
from travel_agent.planning.repair_evaluation import (
    evaluate_repair_cases,
    load_repair_cases,
    repair_report_as_json,
)
from travel_agent.runtime import PlanningRuntime


class ForcedFallbackOptimizer:
    name = "repair-benchmark-v0.5-fallback"

    def solve(self, _problem):
        raise OptimizationTimeoutError("repair benchmark isolates fallback loop")


async def _run(dataset: Path, base_trip_path: Path) -> int:
    runtime = await PlanningRuntime.create(Settings.from_env({}))
    try:
        cases = load_repair_cases(dataset)
        base_trip = TripSpec.model_validate_json(
            base_trip_path.read_text(encoding="utf-8")
        )
        workflow = build_workflow(
            runtime.gateway,
            POIDefaultPolicy(UnknownFactPolicy.ASSUME_WITH_WARNING),
            PlanningPolicy(use_real_walking_routes=False),
            optimizer=ForcedFallbackOptimizer(),
        )
        report = await evaluate_repair_cases(workflow, base_trip, cases)
        print(repair_report_as_json(report))
        return int(
            report.execution_failure_count > 0
            or report.exact_case_accuracy < 1.0
            or report.hard_constraint_satisfaction_rate < 1.0
            or report.bounded_termination_rate < 1.0
        )
    finally:
        await runtime.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="评测违规驱动的局部自修复")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("evals/repairs/cases.jsonl"),
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
