from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from travel_agent.config import Settings
from travel_agent.requirements.evaluation import (
    evaluate_requirement_cases,
    load_requirement_cases,
    report_as_json,
)
from travel_agent.runtime import PlanningRuntime


async def _run(dataset: Path) -> int:
    runtime = await PlanningRuntime.create(Settings.from_env())
    try:
        if runtime.requirement_model is None:
            raise RuntimeError("requirement model is not configured")
        cases = load_requirement_cases(dataset)
        report = await evaluate_requirement_cases(runtime.requirement_model, cases)
        print(report_as_json(report))
        return 0 if report.parse_failure_count == 0 else 1
    finally:
        await runtime.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="评测结构化旅行需求解析")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("evals/requirements/cases.jsonl"),
    )
    args = parser.parse_args()
    return asyncio.run(_run(args.dataset))


if __name__ == "__main__":
    raise SystemExit(main())
