from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LifecycleEvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    intent_match: bool
    grounding_match: bool
    impact_match: bool
    locked_artifacts_preserved: bool
    unaffected_days_preserved: bool
    hard_constraint_regression: bool
    diff_match: bool
    commit_correct: bool
    idempotent: bool
    bounded_termination: bool
    required_route_count: int = Field(default=0, ge=0)
    reused_route_count: int = Field(default=0, ge=0)


class LifecycleEvalReport(BaseModel):
    case_count: int
    intent_exact_match_rate: float
    grounding_accuracy: float
    impact_exact_match_rate: float
    locked_artifact_preservation_rate: float
    unaffected_day_preservation_rate: float
    hard_constraint_regression_rate: float
    diff_exact_match_rate: float
    commit_correctness_rate: float
    idempotent_replay_rate: float
    bounded_termination_rate: float
    route_reuse_rate: float | None


def _rate(cases: list[LifecycleEvalCase], field: str) -> float:
    if not cases:
        return 0.0
    return round(sum(bool(getattr(case, field)) for case in cases) / len(cases), 4)


def evaluate_lifecycle_cases(cases: list[LifecycleEvalCase]) -> LifecycleEvalReport:
    required = sum(case.required_route_count for case in cases)
    reused = sum(case.reused_route_count for case in cases)
    return LifecycleEvalReport(
        case_count=len(cases),
        intent_exact_match_rate=_rate(cases, "intent_match"),
        grounding_accuracy=_rate(cases, "grounding_match"),
        impact_exact_match_rate=_rate(cases, "impact_match"),
        locked_artifact_preservation_rate=_rate(cases, "locked_artifacts_preserved"),
        unaffected_day_preservation_rate=_rate(cases, "unaffected_days_preserved"),
        hard_constraint_regression_rate=round(
            sum(case.hard_constraint_regression for case in cases) / len(cases), 4
        )
        if cases
        else 0.0,
        diff_exact_match_rate=_rate(cases, "diff_match"),
        commit_correctness_rate=_rate(cases, "commit_correct"),
        idempotent_replay_rate=_rate(cases, "idempotent"),
        bounded_termination_rate=_rate(cases, "bounded_termination"),
        route_reuse_rate=round(reused / required, 4) if required else None,
    )

