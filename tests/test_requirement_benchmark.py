from __future__ import annotations

from pathlib import Path

import pytest

from travel_agent.requirements.evaluation import (
    evaluate_requirement_cases,
    load_requirement_cases,
    report_as_json,
)
from travel_agent.requirements.providers.mock import MockRequirementModel


DATASET = Path("evals/requirements/cases.jsonl")


def test_requirement_benchmark_dataset_has_minimum_coverage():
    cases = load_requirement_cases(DATASET)

    assert len(cases) >= 30
    assert {case.category for case in cases} >= {
        "complete",
        "missing_arrival",
        "missing_departure",
        "missing_dates",
        "conflict",
    }


@pytest.mark.asyncio
async def test_mock_requirement_fixture_has_a_reproducible_baseline():
    cases = load_requirement_cases(DATASET)

    report = await evaluate_requirement_cases(MockRequirementModel(), cases)

    assert report.case_count == len(cases)
    assert report.parse_failure_count == 0
    assert report.exact_case_accuracy == 1.0
    assert report.field_accuracy == 1.0
    assert report.blocking_field_precision == 1.0
    assert report.blocking_field_recall == 1.0
    assert report.clarification_route_accuracy == 1.0
    assert all("text" not in case.model_dump() for case in report.cases)
    assert '"provider": "mock"' in report_as_json(report)
