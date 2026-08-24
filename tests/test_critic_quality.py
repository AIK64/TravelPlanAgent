from __future__ import annotations

import pytest

from travel_agent.critique.quality import CriticPolicy, quality_score
from travel_agent.domain.critique_models import (
    DimensionCritique,
    SoftCritique,
    SoftDimension,
)


def _critique(score: int = 80) -> SoftCritique:
    return SoftCritique(
        candidate_id="candidate",
        dimensions=tuple(
            DimensionCritique(
                dimension=dimension,
                score=score,
                summary="评价",
                evidence_ids=("ev_12345678",),
            )
            for dimension in SoftDimension
        ),
        overall_summary="整体",
    )


def test_quality_score_is_deterministic_weighted_sum():
    assert quality_score(_critique(80), CriticPolicy()) == 80


@pytest.mark.parametrize(
    "kwargs",
    [
        {"weights": {SoftDimension.PACE: 1}},
        {"weights": {dimension: 0.1 for dimension in SoftDimension}},
        {"weights": {dimension: (-1 if dimension is SoftDimension.PACE else 0.5) for dimension in SoftDimension}},
        {"quality_threshold": 101},
        {"min_improvement": -1},
        {"max_soft_replan_rounds": 2},
        {"grounding_max_attempts": 3},
    ],
)
def test_critic_policy_rejects_invalid_budgets(kwargs):
    with pytest.raises(ValueError):
        CriticPolicy(**kwargs)


def test_quality_score_rejects_incomplete_dimensions():
    incomplete = _critique().model_copy(update={"dimensions": _critique().dimensions[:1]})
    with pytest.raises(ValueError, match="incomplete"):
        quality_score(incomplete, CriticPolicy())

