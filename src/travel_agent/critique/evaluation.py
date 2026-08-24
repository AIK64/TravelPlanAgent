from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from travel_agent.domain.critique_models import SoftDimension


class SoftCriticEvalCase(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    expected_issue_dimensions: tuple[SoftDimension, ...] = ()
    predicted_issue_dimensions: tuple[SoftDimension, ...] = ()
    referential_grounding_valid: bool
    semantic_grounding_valid: bool
    suggested_action_safe: bool
    hard_constraint_regression: bool = False
    baseline_selected: str
    critic_selected: str
    human_selected: str
    latency_ms: float = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class SoftCriticEvalReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_count: int
    referential_grounding_rate: float
    semantic_grounding_precision: float
    issue_precision: float
    issue_recall: float
    issue_f1: float
    selection_agreement_without_critic: float
    selection_agreement_with_critic: float
    suggested_action_safety_rate: float
    hard_constraint_regression_rate: float
    p50_latency_ms: float
    p95_latency_ms: float
    total_input_tokens: int
    total_output_tokens: int


def evaluate_cases(cases: list[SoftCriticEvalCase]) -> SoftCriticEvalReport:
    if not cases:
        raise ValueError("soft critic evaluation requires at least one case")
    true_positive = false_positive = false_negative = 0
    for case in cases:
        expected = set(case.expected_issue_dimensions)
        predicted = set(case.predicted_issue_dimensions)
        true_positive += len(expected & predicted)
        false_positive += len(predicted - expected)
        false_negative += len(expected - predicted)
    precision = _ratio(true_positive, true_positive + false_positive)
    recall = _ratio(true_positive, true_positive + false_negative)
    f1 = _ratio(2 * precision * recall, precision + recall)
    latencies = sorted(case.latency_ms for case in cases)
    return SoftCriticEvalReport(
        case_count=len(cases),
        referential_grounding_rate=_average(
            case.referential_grounding_valid for case in cases
        ),
        semantic_grounding_precision=_average(
            case.semantic_grounding_valid for case in cases
        ),
        issue_precision=precision,
        issue_recall=recall,
        issue_f1=f1,
        selection_agreement_without_critic=_average(
            case.baseline_selected == case.human_selected for case in cases
        ),
        selection_agreement_with_critic=_average(
            case.critic_selected == case.human_selected for case in cases
        ),
        suggested_action_safety_rate=_average(
            case.suggested_action_safe for case in cases
        ),
        hard_constraint_regression_rate=_average(
            case.hard_constraint_regression for case in cases
        ),
        p50_latency_ms=_percentile(latencies, 0.50),
        p95_latency_ms=_percentile(latencies, 0.95),
        total_input_tokens=sum(case.input_tokens for case in cases),
        total_output_tokens=sum(case.output_tokens for case in cases),
    )


def _average(values) -> float:
    data = [1.0 if value else 0.0 for value in values]
    return round(sum(data) / len(data), 4)


def _ratio(numerator: float, denominator: float) -> float:
    return round(numerator / denominator, 4) if denominator else 1.0


def _percentile(sorted_values: list[float], percentile: float) -> float:
    index = min(len(sorted_values) - 1, int((len(sorted_values) - 1) * percentile + 0.5))
    return round(sorted_values[index], 2)

