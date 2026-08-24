from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import logging
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from travel_agent.evaluation.report import report_as_markdown
from travel_agent.evaluation.runner import run_release_evaluation


async def _run(args) -> int:
    if not args.verbose:
        logging.disable(logging.CRITICAL)
    report = await run_release_evaluation(args.manifest)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output = args.output or ROOT / "reports" / "v1_0" / report.dataset_version / stamp
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(
        report.model_dump_json(indent=2), encoding="utf-8"
    )
    (output / "summary.md").write_text(report_as_markdown(report), encoding="utf-8")
    print(report_as_markdown(report))
    print(f"Report: {output}")
    return 0 if not args.gate or report.gate_passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="运行 v1.0 统一 Agent 发布评测")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=ROOT / "evals" / "v1_0" / "manifest.json",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--profile",
        choices=("mock",),
        default="mock",
        help="默认发布门禁固定为离线 Mock；Live 评测使用独立命令",
    )
    parser.add_argument("--gate", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
