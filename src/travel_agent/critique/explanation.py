from __future__ import annotations

from travel_agent.domain.critique_models import (
    CandidateEvidenceDigest,
    CriticStatus,
    GroundedExplanation,
    GroundedStatement,
    SoftCritique,
)
from travel_agent.domain.models import PlanCandidate


def build_grounded_explanation(
    candidate: PlanCandidate,
    *,
    status: CriticStatus,
    digest: CandidateEvidenceDigest | None,
    critique: SoftCritique | None,
) -> GroundedExplanation:
    evidence_ids = tuple(item.id for item in digest.evidence) if digest else ()
    if status is CriticStatus.SUCCESS and critique is not None:
        ordered = sorted(critique.dimensions, key=lambda item: (-item.score, item.dimension.value))
        highlights = tuple(
            GroundedStatement(text=item.summary, evidence_ids=item.evidence_ids)
            for item in ordered[:3]
        )
        tradeoff_source = min(
            critique.dimensions,
            key=lambda item: (item.score, item.dimension.value),
        )
        tradeoffs = (
            GroundedStatement(
                text=f"需要权衡：{tradeoff_source.summary}",
                evidence_ids=tradeoff_source.evidence_ids,
            ),
        )
        return GroundedExplanation(
            candidate_id=candidate.id,
            headline=f"{candidate.style.value} 方案在硬约束合法基础上获得更好的软质量评价",
            highlights=highlights,
            tradeoffs=tradeoffs,
            critic_status=status,
        )

    fallback_ids = evidence_ids[:2]
    if not fallback_ids:
        raise ValueError("deterministic explanation requires an evidence digest")
    return GroundedExplanation(
        candidate_id=candidate.id,
        headline="软评审不可用，已按硬约束与确定性指标选择方案",
        highlights=(
            GroundedStatement(
                text=(
                    f"方案总移动时间 {candidate.metrics.total_travel_minutes} 分钟，"
                    f"偏好匹配度 {candidate.metrics.preference_match:.0%}。"
                ),
                evidence_ids=fallback_ids,
            ),
        ),
        critic_status=status,
    )
