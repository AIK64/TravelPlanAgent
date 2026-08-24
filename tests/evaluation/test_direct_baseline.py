from pathlib import Path
import json

import pytest

from travel_agent.evaluation.baselines import (
    DirectBaselineCase,
    MockDirectPlanModel,
    run_direct_plan_baseline,
)


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_mock_direct_baseline_is_explicitly_contract_only_and_post_validated():
    raw = json.loads(
        (ROOT / "evals" / "v1_0" / "baselines" / "direct_plan_cases.jsonl")
        .read_text(encoding="utf-8")
        .strip()
    )
    case = DirectBaselineCase.model_validate(
        {key: value for key, value in raw.items() if key != "mock_candidate"}
    )
    report = await run_direct_plan_baseline(
        MockDirectPlanModel({raw["case_id"]: raw["mock_candidate"]}),
        (case,),
        evidence_level="annotated_contract",
    )

    assert report.case_count == 1
    assert report.evidence_level == "annotated_contract"
    assert report.unsafe_delivery_count == 1
    assert report.total_input_tokens is None
