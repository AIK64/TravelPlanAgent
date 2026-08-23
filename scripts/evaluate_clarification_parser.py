from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from travel_agent.config import Settings
from travel_agent.requirements.clarification_evaluation import (
    clarification_report_as_json,
    evaluate_clarification_cases,
    load_clarification_cases,
)
from travel_agent.runtime import PlanningRuntime


async def _run(dataset: Path) -> int:
    runtime = await PlanningRuntime.create(Settings.from_env())
    try:
        if runtime.requirement_model is None:
            raise RuntimeError("requirement model is not configured")
        cases = load_clarification_cases(dataset)
        report = await evaluate_clarification_cases(
            runtime.requirement_model,
            cases,
        )
        print(clarification_report_as_json(report))
        return 0 if report.patch_failure_count == 0 else 1
    finally:
        await runtime.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="评测多轮需求澄清 Patch")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("evals/clarifications/cases.jsonl"),
    )
    args = parser.parse_args()
    return asyncio.run(_run(args.dataset))


if __name__ == "__main__":
    raise SystemExit(main())
