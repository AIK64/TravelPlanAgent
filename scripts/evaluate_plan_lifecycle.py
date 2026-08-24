from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from travel_agent.lifecycle.evaluation import LifecycleEvalCase, evaluate_lifecycle_cases


def main() -> None:
    parser = argparse.ArgumentParser(description="评估 v0.8 计划生命周期离线 Fixture")
    parser.add_argument(
        "--cases",
        type=Path,
        default=ROOT / "evals" / "lifecycle" / "cases.jsonl",
    )
    args = parser.parse_args()
    cases = [
        LifecycleEvalCase.model_validate_json(line)
        for line in args.cases.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    report = evaluate_lifecycle_cases(cases)
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

