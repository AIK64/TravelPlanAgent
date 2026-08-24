from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from travel_agent.evaluation.baselines import (
    DeepSeekDirectPlanModel,
    DirectBaselineCase,
    MockDirectPlanModel,
    run_direct_plan_baseline,
)


def _load(path: Path):
    raw = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    cases = tuple(
        DirectBaselineCase.model_validate(
            {key: value for key, value in item.items() if key != "mock_candidate"}
        )
        for item in raw
    )
    fixtures = {
        item["case_id"]: item["mock_candidate"]
        for item in raw
        if "mock_candidate" in item
    }
    return cases, fixtures


async def _run(args) -> int:
    cases, fixtures = _load(args.dataset)
    client = None
    if args.provider == "mock":
        model = MockDirectPlanModel(fixtures)
        evidence_level = "annotated_contract"
    else:
        if not args.allow_live:
            raise SystemExit("DeepSeek 会产生真实 API 调用和费用；请显式增加 --allow-live")
        api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        model_name = args.model or os.environ.get("DEEPSEEK_MODEL", "").strip()
        if not api_key or not model_name:
            raise SystemExit("需要 DEEPSEEK_API_KEY 和显式 --model/DEEPSEEK_MODEL")
        try:
            from openai import AsyncOpenAI
        except ImportError as error:
            raise SystemExit('请先安装 ".[llm-deepseek]" 可选依赖') from error
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            max_retries=0,
        )
        model = DeepSeekDirectPlanModel(client=client, model=model_name)
        evidence_level = "live_provider"
    try:
        report = await run_direct_plan_baseline(
            model,
            cases,
            evidence_level=evidence_level,
        )
    finally:
        if client is not None:
            await client.close()
    print(report.model_dump_json(indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="运行单次 LLM 旅行计划 Baseline")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=ROOT / "evals" / "v1_0" / "baselines" / "direct_plan_cases.jsonl",
    )
    parser.add_argument("--provider", choices=("mock", "deepseek"), default="mock")
    parser.add_argument("--model")
    parser.add_argument("--allow-live", action="store_true")
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
