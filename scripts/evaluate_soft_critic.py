from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from travel_agent.critique.evaluation import SoftCriticEvalCase, evaluate_cases


def main() -> None:
    parser = argparse.ArgumentParser(description="评估 Grounded Soft Critic 离线标注集")
    parser.add_argument(
        "--cases",
        type=Path,
        default=ROOT / "evals" / "soft_critic" / "cases.jsonl",
    )
    args = parser.parse_args()
    cases = [
        SoftCriticEvalCase.model_validate_json(line)
        for line in args.cases.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    report = evaluate_cases(cases)
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

