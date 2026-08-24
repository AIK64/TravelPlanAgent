from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import json
import logging
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from travel_agent.evaluation.ablations import run_ablation_evaluation


def _markdown(report) -> str:
    rows = "\n".join(
        "| {variant} | {cases} | {hard:.4f} | {unsafe} | {tools} | {attempts} | {hits} | {llm} |".format(
            variant=item.variant.value,
            cases=item.case_count,
            hard=item.hard_constraint_satisfaction_rate,
            unsafe=item.unsafe_delivery_count,
            tools=item.total_tool_calls,
            attempts=item.total_provider_attempts,
            hits=item.total_cache_hits,
            llm=item.total_llm_calls,
        )
        for item in report.variants
    )
    gates = "\n".join(
        f"| {item.name} | {'PASS' if item.passed else 'FAIL'} | {item.actual} | {item.expected} |"
        for item in report.comparison_gates
    )
    return f"""# v1.0 Ablation Evaluation

- Dataset: `{report.dataset_version}`
- Workflow executions: {report.workflow_execution_count}
- Gate: {'PASS' if report.gate_passed else 'FAIL'}
- Reproducibility: `{report.provenance.reproducibility_fingerprint}`

| Variant | Cases | Hard rate | Unsafe | Tool calls | Provider attempts | Cache hits | LLM calls |
|---|---:|---:|---:|---:|---:|---:|---:|
{rows}

| Comparison gate | Result | Actual | Expected |
|---|---:|---:|---:|
{gates}
"""


async def _run(args) -> int:
    if not args.verbose:
        logging.disable(logging.CRITICAL)
    report = await run_ablation_evaluation(args.manifest, random_seed=args.seed)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output = args.output or ROOT / "reports" / "v1_0" / "ablations" / stamp
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(
        report.model_dump_json(indent=2), encoding="utf-8"
    )
    summary = _markdown(report)
    (output / "summary.md").write_text(summary, encoding="utf-8")
    print(summary)
    print(f"Report: {output}")
    return 0 if not args.gate or report.gate_passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="运行 v1.0 实际工作流消融评测")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=ROOT / "evals" / "v1_0" / "manifest.json",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--gate", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
