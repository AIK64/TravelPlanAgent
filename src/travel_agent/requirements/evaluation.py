from __future__ import annotations

from collections.abc import Sequence
from datetime import date
import json
from pathlib import Path
from time import perf_counter
from typing import Any

from pydantic import BaseModel, Field

from travel_agent.requirements.models import NaturalPlanningRequest
from travel_agent.requirements.protocols import RequirementModel
from travel_agent.requirements.validation import validate_requirement


class RequirementBenchmarkCase(BaseModel):
    id: str
    category: str
    text: str = Field(min_length=1)
    reference_date: date
    timezone: str = "Asia/Shanghai"
    expected_fields: dict[str, Any] = Field(default_factory=dict)
    expected_blocking_fields: list[str] = Field(default_factory=list)
    expected_needs_clarification: bool


class RequirementCaseResult(BaseModel):
    id: str
    category: str
    exact: bool
    mismatched_fields: list[str] = Field(default_factory=list)
    expected_blocking_fields: list[str] = Field(default_factory=list)
    actual_blocking_fields: list[str] = Field(default_factory=list)
    clarification_correct: bool
    elapsed_ms: float = Field(ge=0)
    error_type: str | None = None


class RequirementBenchmarkReport(BaseModel):
    provider: str
    model: str
    case_count: int
    parse_failure_count: int
    exact_case_accuracy: float
    field_accuracy: float
    blocking_field_precision: float
    blocking_field_recall: float
    clarification_route_accuracy: float
    average_elapsed_ms: float
    cases: list[RequirementCaseResult]


def load_requirement_cases(path: Path) -> list[RequirementBenchmarkCase]:
    cases: list[RequirementBenchmarkCase] = []
    with path.open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                cases.append(RequirementBenchmarkCase.model_validate_json(line))
            except Exception as error:
                raise ValueError(
                    f"invalid requirement benchmark case at line {line_number}"
                ) from error
    if not cases:
        raise ValueError("requirement benchmark dataset must not be empty")
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("requirement benchmark case ids must be unique")
    return cases


def _field_value(payload: dict[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


async def evaluate_requirement_cases(
    model: RequirementModel,
    cases: Sequence[RequirementBenchmarkCase],
) -> RequirementBenchmarkReport:
    if not cases:
        raise ValueError("cases must not be empty")

    results: list[RequirementCaseResult] = []
    field_total = 0
    field_correct = 0
    true_positive = 0
    false_positive = 0
    false_negative = 0
    route_correct = 0
    parse_failures = 0
    total_elapsed_ms = 0.0

    for case in cases:
        started = perf_counter()
        mismatched_fields: list[str] = []
        actual_blocking: set[str] = set()
        error_type: str | None = None
        field_total += len(case.expected_fields)
        try:
            output = await model.parse(
                NaturalPlanningRequest(
                    text=case.text,
                    reference_date=case.reference_date,
                    timezone=case.timezone,
                )
            )
            payload = output.draft.model_dump(mode="json")
            for path, expected in case.expected_fields.items():
                if _field_value(payload, path) == expected:
                    field_correct += 1
                else:
                    mismatched_fields.append(path)
            actual_blocking = {
                issue.field
                for issue in validate_requirement(
                    output.draft,
                    timezone_name=case.timezone,
                )
                if issue.blocking
            }
        except Exception as error:  # benchmark must report provider failures
            parse_failures += 1
            error_type = type(error).__name__
            mismatched_fields = list(case.expected_fields)

        expected_blocking = set(case.expected_blocking_fields)
        true_positive += len(actual_blocking & expected_blocking)
        false_positive += len(actual_blocking - expected_blocking)
        false_negative += len(expected_blocking - actual_blocking)
        clarification_correct = (
            bool(actual_blocking) == case.expected_needs_clarification
            and error_type is None
        )
        route_correct += int(clarification_correct)
        elapsed_ms = round((perf_counter() - started) * 1000, 2)
        total_elapsed_ms += elapsed_ms
        exact = (
            not mismatched_fields
            and actual_blocking == expected_blocking
            and clarification_correct
            and error_type is None
        )
        results.append(
            RequirementCaseResult(
                id=case.id,
                category=case.category,
                exact=exact,
                mismatched_fields=mismatched_fields,
                expected_blocking_fields=sorted(expected_blocking),
                actual_blocking_fields=sorted(actual_blocking),
                clarification_correct=clarification_correct,
                elapsed_ms=elapsed_ms,
                error_type=error_type,
            )
        )

    precision_denominator = true_positive + false_positive
    recall_denominator = true_positive + false_negative
    return RequirementBenchmarkReport(
        provider=model.name,
        model=model.model,
        case_count=len(cases),
        parse_failure_count=parse_failures,
        exact_case_accuracy=sum(result.exact for result in results) / len(cases),
        field_accuracy=(field_correct / field_total if field_total else 1.0),
        blocking_field_precision=(
            true_positive / precision_denominator if precision_denominator else 1.0
        ),
        blocking_field_recall=(
            true_positive / recall_denominator if recall_denominator else 1.0
        ),
        clarification_route_accuracy=route_correct / len(cases),
        average_elapsed_ms=round(total_elapsed_ms / len(cases), 2),
        cases=results,
    )


def report_as_json(report: RequirementBenchmarkReport) -> str:
    return json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2)
