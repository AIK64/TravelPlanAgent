from __future__ import annotations

import argparse
from pathlib import Path

from travel_agent.evaluation.memory import load_memory_scenarios, run_memory_ablations


def main() -> None:
    parser = argparse.ArgumentParser(description="运行 v1.1 Memory 消融评测")
    parser.add_argument(
        "--dataset", default="evals/v1_1/memory_scenarios.jsonl"
    )
    parser.add_argument(
        "--output", default="reports/v1_1-memory-ablation.json"
    )
    args = parser.parse_args()
    scenarios = load_memory_scenarios(args.dataset)
    report = run_memory_ablations(scenarios)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    print(f"memory ablation report: {target} ({report.scenarios} scenarios)")


if __name__ == "__main__":
    main()
