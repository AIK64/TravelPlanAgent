from __future__ import annotations

from pathlib import Path

import pytest

from travel_agent.requirements.clarification_evaluation import (
    evaluate_clarification_cases,
    load_clarification_cases,
)
from travel_agent.requirements.providers.mock import MockRequirementModel


DATASET = Path("evals/clarifications/cases.jsonl")


@pytest.mark.asyncio
async def test_mock_clarification_benchmark_is_reproducible():
    cases = load_clarification_cases(DATASET)

    report = await evaluate_clarification_cases(MockRequirementModel(), cases)

    assert report.case_count == 6
    assert report.patch_failure_count == 0
    assert report.exact_case_accuracy == 1.0
    assert report.target_field_accuracy == 1.0
    assert report.field_preservation_rate == 1.0
