from __future__ import annotations

from travel_agent.critique.prompts import CRITIC_PROMPT_VERSION
from travel_agent.domain.critique_models import (
    DimensionCritique,
    EvidenceKind,
    SoftCritique,
    SoftCriticProviderOutput,
    SoftCriticRequest,
    SoftDimension,
)


class MockCriticModel:
    """确定性离线 Fixture，只用于 Graph、Grounding 与消融回归。"""

    name = "mock"
    model = "mock-soft-critic-v1"
    prompt_version = CRITIC_PROMPT_VERSION

    async def critique(self, request: SoftCriticRequest) -> SoftCriticProviderOutput:
        critiques: list[SoftCritique] = []
        for digest in request.digests:
            by_field = {item.field: item for item in digest.evidence}
            preference = float(by_field.get("preference_match").value) if by_field.get("preference_match") else 0.7
            diversity = float(by_field.get("diversity").value) if by_field.get("diversity") else 0.7
            fatigue = float(by_field.get("fatigue_score").value) if by_field.get("fatigue_score") else 0.4
            travel = float(by_field.get("total_travel_minutes").value) if by_field.get("total_travel_minutes") else 120
            scores = {
                SoftDimension.PACE: max(70, round(100 - fatigue * 30)),
                SoftDimension.INTEREST_COVERAGE: max(70, round(preference * 100)),
                SoftDimension.DIVERSITY: max(70, round(diversity * 100)),
                SoftDimension.REST_FRIENDLINESS: max(70, round(100 - fatigue * 25)),
                SoftDimension.GEOGRAPHIC_COHERENCE: max(70, round(95 - min(travel, 600) / 20)),
            }
            fallback_id = digest.evidence[0].id
            dimension_refs = {
                SoftDimension.PACE: _refs(digest, {"fatigue_score", "activity_count"}, fallback_id),
                SoftDimension.INTEREST_COVERAGE: _refs(digest, {"interests", "preference_match", "categories"}, fallback_id),
                SoftDimension.DIVERSITY: _refs(digest, {"diversity", "categories"}, fallback_id),
                SoftDimension.REST_FRIENDLINESS: _refs(digest, {"needs_frequent_rest", "fatigue_score", "activity_minutes"}, fallback_id),
                SoftDimension.GEOGRAPHIC_COHERENCE: _refs(digest, {"total_travel_minutes", "distance_meters_from_previous"}, fallback_id),
            }
            dimensions = tuple(
                DimensionCritique(
                    dimension=dimension,
                    score=scores[dimension],
                    summary=f"{dimension.value} 基于标准化事实的离线确定性评价为 {scores[dimension]} 分",
                    evidence_ids=dimension_refs[dimension],
                )
                for dimension in SoftDimension
            )
            critiques.append(
                SoftCritique(
                    candidate_id=digest.candidate_id,
                    dimensions=dimensions,
                    overall_summary="离线 Mock 仅验证软评审闭环，不代表真实模型质量。",
                    tradeoff_evidence_ids=(fallback_id,),
                )
            )
        return SoftCriticProviderOutput(critiques=tuple(critiques))


def _refs(digest, fields: set[str], fallback_id: str) -> tuple[str, ...]:
    ids = tuple(item.id for item in digest.evidence if item.field in fields)[:3]
    return ids or (fallback_id,)

