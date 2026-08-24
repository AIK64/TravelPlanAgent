from __future__ import annotations

from datetime import date

from travel_agent.critique.grounding import validate_critique_grounding
from travel_agent.domain.critique_models import (
    CandidateEvidenceDigest,
    DimensionCritique,
    EvidenceItem,
    EvidenceKind,
    SoftCritique,
    SoftDimension,
    SuggestedActionKind,
    SuggestedSoftAction,
)
from travel_agent.domain.models import PlanStyle


def _digest() -> CandidateEvidenceDigest:
    evidence = EvidenceItem(
        id="ev_1234567890",
        kind=EvidenceKind.SCHEDULE_FACT,
        candidate_id="candidate-1",
        day=date(2026, 10, 2),
        entity_id="poi-1",
        field="must_visit",
        value=True,
        source="candidate_schedule",
        confidence=1,
    )
    return CandidateEvidenceDigest(
        candidate_id="candidate-1",
        style=PlanStyle.BALANCED,
        evidence=(evidence,),
        input_chars=100,
    )


def _critique(evidence_id: str = "ev_1234567890") -> SoftCritique:
    return SoftCritique(
        candidate_id="candidate-1",
        dimensions=tuple(
            DimensionCritique(
                dimension=dimension,
                score=80,
                summary="有证据的评价",
                evidence_ids=(evidence_id,),
            )
            for dimension in SoftDimension
        ),
        overall_summary="整体合理",
        tradeoff_evidence_ids=(evidence_id,),
    )


def test_grounding_accepts_complete_same_candidate_references():
    assert validate_critique_grounding((_digest(),), (_critique(),)) == ()


def test_grounding_rejects_unknown_reference():
    errors = validate_critique_grounding((_digest(),), (_critique("ev_unknown"),))
    assert any(error.startswith("unknown_evidence") for error in errors)


def test_grounding_rejects_removing_must_visit():
    base = _critique()
    first = base.dimensions[0].model_copy(
        update={
            "suggested_action": SuggestedSoftAction(
                kind=SuggestedActionKind.REMOVE_OPTIONAL_POI,
                poi_id="poi-1",
                from_day=date(2026, 10, 2),
                evidence_ids=("ev_1234567890",),
                expected_dimension=base.dimensions[0].dimension,
            )
        }
    )
    critique = base.model_copy(
        update={"dimensions": (first, *base.dimensions[1:])}
    )
    errors = validate_critique_grounding((_digest(),), (critique,))
    assert "action_removes_must_visit:candidate-1:poi-1" in errors


def test_grounding_rejects_candidate_dimension_and_tradeoff_mismatches():
    base = _critique()
    duplicate = base.model_copy(
        update={
            "dimensions": (
                base.dimensions[0],
                base.dimensions[0],
                *base.dimensions[2:],
            ),
            "tradeoff_evidence_ids": ("ev_unknown",),
        }
    )
    errors = validate_critique_grounding((_digest(),), (duplicate,))
    assert "dimension_set_invalid:candidate-1" in errors
    assert any(error.startswith("duplicate_dimension") for error in errors)
    assert any(error.startswith("tradeoff_unknown_evidence") for error in errors)

    wrong_candidate = base.model_copy(update={"candidate_id": "candidate-2"})
    errors = validate_critique_grounding((_digest(),), (wrong_candidate,))
    assert "candidate_set_mismatch" in errors
    assert "unknown_candidate:candidate-2" in errors


def test_grounding_rejects_unknown_action_entity_and_target_day():
    base = _critique()
    unknown_action = SuggestedSoftAction(
        kind=SuggestedActionKind.MOVE_OPTIONAL_POI,
        poi_id="poi-unknown",
        from_day=date(2026, 10, 2),
        to_day=date(2026, 10, 3),
        evidence_ids=("ev_unknown",),
        expected_dimension=SoftDimension.PACE,
    )
    first = base.dimensions[0].model_copy(
        update={"suggested_action": unknown_action}
    )
    critique = base.model_copy(
        update={"dimensions": (first, *base.dimensions[1:])}
    )
    errors = validate_critique_grounding((_digest(),), (critique,))
    assert any(error.startswith("action_unknown_evidence") for error in errors)
    assert "action_unknown_poi:candidate-1:poi-unknown" in errors
    assert "action_invalid_from_day:candidate-1:poi-unknown" in errors
    assert "action_invalid_to_day:candidate-1:poi-unknown" in errors
