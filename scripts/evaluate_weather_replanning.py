from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from travel_agent.weather.evaluation import WeatherEvalCase, evaluate_weather_cases


def main() -> None:
    parser = argparse.ArgumentParser(description="评估 v0.9 天气事件局部重规划 Fixture")
    parser.add_argument(
        "--cases",
        type=Path,
        default=ROOT / "evals" / "weather" / "cases.jsonl",
    )
    parser.add_argument("--dataset-version", default="weather-fixture-v1")
    args = parser.parse_args()
    cases = [
        WeatherEvalCase.model_validate_json(line)
        for line in args.cases.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    report = evaluate_weather_cases(cases, dataset_version=args.dataset_version)
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
