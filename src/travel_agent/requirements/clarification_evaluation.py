from __future__ import annotations

from collections.abc import Sequence
from datetime import date
import json
from pathlib import Path
from time import perf_counter
from typing import Any

from pydantic import BaseModel, Field

from travel_agent.requirements.clarification import merge_requirement_patch
from travel_agent.requirements.models import (
    ClarificationModelInput,
    RequirementDraft,
    RequirementIssue,
)
from travel_agent.requirements.protocols import RequirementModel


class ClarificationBenchmarkCase(BaseModel):
    id: str
    category: str
    answer: str = Field(min_length=1)
    current_draft: RequirementDraft
    target_fields: list[str] = Field(min_length=1)
    issues: list[RequirementIssue] = Field(min_length=1)
    reference_date: date
    timezone: str = "Asia/Shanghai"
    expected_fields: dict[str, Any] = Field(default_factory=dict)


class ClarificationCaseResult(BaseModel):
    id: str
    category: str
    exact: bool
    mismatched_fields: list[str] = Field(default_factory=list)
    preservation_correct: bool
    elapsed_ms: float = Field(ge=0)
    error_type: str | None = None


class ClarificationBenchmarkReport(BaseModel):
    provider: str
    model: str
    case_count: int
    patch_failure_count: int
    exact_case_accuracy: float
    target_field_accuracy: float
    field_preservation_rate: float
    average_elapsed_ms: float
    cases: list[ClarificationCaseResult]


def load_clarification_cases(path: Path) -> list[ClarificationBenchmarkCase]:
    cases: list[ClarificationBenchmarkCase] = []
    with path.open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                cases.append(ClarificationBenchmarkCase.model_validate_json(line))
            except Exception as error:
                raise ValueError(
                    f"invalid clarification benchmark case at line {line_number}"
                ) from error
    if not cases:
        raise ValueError("clarification benchmark dataset must not be empty")
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("clarification benchmark case ids must be unique")
    return cases


async def evaluate_clarification_cases(
    model: RequirementModel,
    cases: Sequence[ClarificationBenchmarkCase],
) -> ClarificationBenchmarkReport:
    if not cases:
        raise ValueError("cases must not be empty")

    results: list[ClarificationCaseResult] = []
    field_total = 0
    field_correct = 0
    preserved_cases = 0
    patch_failures = 0
    total_elapsed_ms = 0.0
    for case in cases:
        started = perf_counter()
        mismatched: list[str] = []
        error_type: str | None = None
        preservation_correct = False
        field_total += len(case.expected_fields)
        try:
            output = await model.parse_clarification(
                ClarificationModelInput(
                    answer=case.answer,
                    current_draft=case.current_draft,
                    target_fields=case.target_fields,
                    issues=case.issues,
                    reference_date=case.reference_date,
                    timezone=case.timezone,
                )
            )
            merged, _, _ = merge_requirement_patch(
                case.current_draft,
                output.patch,
                allowed_fields=case.target_fields,
            )
            merged_payload = merged.model_dump(mode="json")
            for path, expected in case.expected_fields.items():
                if _field_value(merged_payload, path) == expected:
                    field_correct += 1
                else:
                    mismatched.append(path)
            preservation_correct = _preserves_non_target_fields(
                case.current_draft,
                merged,
                set(case.target_fields),
            )
            preserved_cases += int(preservation_correct)
        except Exception as error:  # benchmark records provider failures
            patch_failures += 1
            error_type = type(error).__name__
            mismatched = list(case.expected_fields)

        elapsed_ms = round((perf_counter() - started) * 1000, 2)
        total_elapsed_ms += elapsed_ms
        results.append(
            ClarificationCaseResult(
                id=case.id,
                category=case.category,
                exact=not mismatched and preservation_correct and error_type is None,
                mismatched_fields=mismatched,
                preservation_correct=preservation_correct,
                elapsed_ms=elapsed_ms,
                error_type=error_type,
            )
        )

    return ClarificationBenchmarkReport(
        provider=model.name,
        model=model.model,
        case_count=len(cases),
        patch_failure_count=patch_failures,
        exact_case_accuracy=sum(item.exact for item in results) / len(cases),
        target_field_accuracy=(field_correct / field_total if field_total else 1.0),
        field_preservation_rate=preserved_cases / len(cases),
        average_elapsed_ms=round(total_elapsed_ms / len(cases), 2),
        cases=results,
    )


def clarification_report_as_json(report: ClarificationBenchmarkReport) -> str:
    return json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2)


def _field_value(payload: dict[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _preserves_non_target_fields(
    before: RequirementDraft,
    after: RequirementDraft,
    target_fields: set[str],
) -> bool:
    before_payload = before.model_dump(mode="json")
    after_payload = after.model_dump(mode="json")
    for field in RequirementDraft.model_fields:
        if field in target_fields:
            continue
        nested_targets = {
            path.split(".", maxsplit=1)[1]
            for path in target_fields
            if path.startswith(f"{field}.")
        }
        if not nested_targets:
            if before_payload[field] != after_payload[field]:
                return False
            continue
        before_nested = before_payload[field] or {}
        after_nested = after_payload[field] or {}
        for nested_field in {"name", "at"} - nested_targets:
            if before_nested.get(nested_field) != after_nested.get(nested_field):
                return False
    return True
