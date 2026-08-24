from __future__ import annotations

from dataclasses import dataclass, field

from travel_agent.domain.critique_models import SoftCritique, SoftDimension
from travel_agent.domain.models import PlanCandidate, ValidationStatus


@dataclass(frozen=True, slots=True)
class CriticPolicy:
    weights: dict[SoftDimension, float] = field(
        default_factory=lambda: {
            SoftDimension.PACE: 0.25,
            SoftDimension.INTEREST_COVERAGE: 0.25,
            SoftDimension.DIVERSITY: 0.15,
            SoftDimension.REST_FRIENDLINESS: 0.15,
            SoftDimension.GEOGRAPHIC_COHERENCE: 0.20,
        }
    )
    quality_threshold: float = 70
    min_improvement: float = 5
    max_soft_replan_rounds: int = 1
    grounding_max_attempts: int = 2

    def __post_init__(self) -> None:
        if set(self.weights) != set(SoftDimension):
            raise ValueError("critic weights must cover every soft dimension")
        if abs(sum(self.weights.values()) - 1.0) > 1e-9:
            raise ValueError("critic weights must sum to 1")
        if any(weight < 0 for weight in self.weights.values()):
            raise ValueError("critic weights must be non-negative")
        if not 0 <= self.quality_threshold <= 100:
            raise ValueError("quality_threshold must be between 0 and 100")
        if not 0 <= self.min_improvement <= 100:
            raise ValueError("min_improvement must be between 0 and 100")
        if not 0 <= self.max_soft_replan_rounds <= 1:
            raise ValueError("v0.7 supports at most one soft replan round")
        if self.grounding_max_attempts < 1 or self.grounding_max_attempts > 2:
            raise ValueError("grounding_max_attempts must be 1 or 2")


def quality_score(critique: SoftCritique, policy: CriticPolicy) -> float:
    scores = {item.dimension: item.score for item in critique.dimensions}
    if set(scores) != set(SoftDimension):
        raise ValueError("cannot score an incomplete critique")
    return round(
        sum(scores[dimension] * policy.weights[dimension] for dimension in SoftDimension),
        2,
    )


def quality_scores(
    critiques: tuple[SoftCritique, ...],
    policy: CriticPolicy,
) -> dict[str, float]:
    return {critique.candidate_id: quality_score(critique, policy) for critique in critiques}


def select_by_quality(
    candidates: list[PlanCandidate],
    scores: dict[str, float],
) -> PlanCandidate:
    """硬验证等级始终先于 Grounded Soft Score。"""
    return min(
        candidates,
        key=lambda candidate: (
            0
            if candidate.validation is not None
            and candidate.validation.status is ValidationStatus.VALID
            else 1,
            -(scores.get(candidate.id, float("-inf"))),
            -(candidate.score if candidate.score is not None else float("-inf")),
            candidate.id,
        ),
    )

