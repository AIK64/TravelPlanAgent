from __future__ import annotations

import json
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from travel_agent.domain.models import PlanCandidate, TripSpec
from travel_agent.planning.validator import validate_candidate
from travel_agent.requirements.models import NaturalPlanningRequest
from travel_agent.requirements.providers._compat import usage_value


DIRECT_PLAN_PROMPT_VERSION = "direct-plan-baseline-v1"
DIRECT_PLAN_SYSTEM_PROMPT = """你是单次旅行规划 Baseline。
只根据用户需求和给定的冻结 EvidenceBundle 输出一个 PlanCandidate JSON。
不得调用工具，不得请求补充信息，不得看到 Validator、Critic 或 Repair 反馈。
严格满足 JSON Schema；未知事实不要自行编造。"""


class DirectPlanOutput(BaseModel):
    """单次 LLM Baseline 的结构化边界；Validator 只由 Evaluator 事后调用。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate: PlanCandidate
    input_tokens: int | None = None
    output_tokens: int | None = None


class DirectPlanModel(Protocol):
    name: str
    model: str
    prompt_version: str

    async def generate(
        self, request: NaturalPlanningRequest, evidence_bundle: dict
    ) -> DirectPlanOutput: ...


class MockDirectPlanModel:
    """仅验证 Baseline Runner Contract，不声明真实模型质量。"""

    name = "mock"
    model = "mock-direct-plan-v1"
    prompt_version = DIRECT_PLAN_PROMPT_VERSION

    def __init__(self, fixtures: dict[str, PlanCandidate | dict]) -> None:
        self._fixtures = fixtures

    async def generate(
        self, request: NaturalPlanningRequest, evidence_bundle: dict
    ) -> DirectPlanOutput:
        fixture_id = str(evidence_bundle.get("fixture_id", ""))
        if fixture_id not in self._fixtures:
            raise KeyError(f"missing direct baseline fixture: {fixture_id}")
        candidate = self._fixtures[fixture_id]
        if not isinstance(candidate, PlanCandidate):
            candidate = PlanCandidate.model_validate(candidate)
        return DirectPlanOutput(candidate=candidate)


class DeepSeekDirectPlanModel:
    """OpenAI-compatible DeepSeek JSON Output；调用必须由用户显式启动。"""

    name = "deepseek"
    prompt_version = DIRECT_PLAN_PROMPT_VERSION

    def __init__(self, *, client: Any, model: str, max_tokens: int = 8_192) -> None:
        if not model.strip():
            raise ValueError("model must be explicit")
        if max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        self._client = client
        self.model = model
        self._max_tokens = max_tokens

    async def generate(
        self, request: NaturalPlanningRequest, evidence_bundle: dict
    ) -> DirectPlanOutput:
        schema = json.dumps(
            PlanCandidate.model_json_schema(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        system = f"{DIRECT_PLAN_SYSTEM_PROMPT}\nPlanCandidate JSON Schema:\n{schema}"
        user = json.dumps(
            {
                "request": request.model_dump(mode="json"),
                "evidence_bundle": evidence_bundle,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        response = await self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            max_tokens=self._max_tokens,
            extra_body={"thinking": {"type": "disabled"}},
        )
        choices = getattr(response, "choices", None)
        if not isinstance(choices, list) or not choices:
            raise ValueError("direct baseline returned no choice")
        choice = choices[0]
        if getattr(choice, "finish_reason", None) not in {"stop", None}:
            raise ValueError("direct baseline did not return complete JSON")
        message = getattr(choice, "message", None)
        content = getattr(message, "content", None) if message is not None else None
        if not isinstance(content, str) or not content.strip():
            raise ValueError("direct baseline returned empty content")
        candidate = PlanCandidate.model_validate_json(content, strict=True)
        usage = getattr(response, "usage", None)
        return DirectPlanOutput(
            candidate=candidate,
            input_tokens=usage_value(usage, "prompt_tokens"),
            output_tokens=usage_value(usage, "completion_tokens"),
        )


class DirectBaselineCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    request: NaturalPlanningRequest
    trip: TripSpec
    evidence_bundle: dict
    evidence_tool_calls: int = Field(default=0, ge=0)
    evidence_provider_attempts: int = Field(default=0, ge=0)


class DirectBaselineCaseResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    hard_constraints_satisfied: bool
    input_tokens: int | None
    output_tokens: int | None
    evidence_tool_calls: int
    evidence_provider_attempts: int


class DirectBaselineReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    model: str
    prompt_version: str
    evidence_level: str
    case_count: int
    hard_constraint_satisfaction_rate: float
    unsafe_delivery_count: int
    total_input_tokens: int | None
    total_output_tokens: int | None
    total_evidence_tool_calls: int
    total_evidence_provider_attempts: int
    cases: tuple[DirectBaselineCaseResult, ...]


async def run_direct_plan_baseline(
    model: DirectPlanModel,
    cases: tuple[DirectBaselineCase, ...],
    *,
    evidence_level: str,
) -> DirectBaselineReport:
    """每个 Case 恰好调用模型一次；独立 Validator 的结果不会回写模型。"""
    results: list[DirectBaselineCaseResult] = []
    for case in cases:
        output = await model.generate(case.request, dict(case.evidence_bundle))
        validation = validate_candidate(case.trip, output.candidate, [])
        results.append(
            DirectBaselineCaseResult(
                case_id=case.case_id,
                hard_constraints_satisfied=validation.valid,
                input_tokens=output.input_tokens,
                output_tokens=output.output_tokens,
                evidence_tool_calls=case.evidence_tool_calls,
                evidence_provider_attempts=case.evidence_provider_attempts,
            )
        )
    valid_count = sum(item.hard_constraints_satisfied for item in results)
    return DirectBaselineReport(
        provider=model.name,
        model=model.model,
        prompt_version=model.prompt_version,
        evidence_level=evidence_level,
        case_count=len(results),
        hard_constraint_satisfaction_rate=(
            round(valid_count / len(results), 4) if results else 1.0
        ),
        unsafe_delivery_count=len(results) - valid_count,
        total_input_tokens=_optional_sum(item.input_tokens for item in results),
        total_output_tokens=_optional_sum(item.output_tokens for item in results),
        total_evidence_tool_calls=sum(item.evidence_tool_calls for item in results),
        total_evidence_provider_attempts=sum(
            item.evidence_provider_attempts for item in results
        ),
        cases=tuple(results),
    )


def _optional_sum(values) -> int | None:
    items = list(values)
    return sum(items) if all(item is not None for item in items) else None
