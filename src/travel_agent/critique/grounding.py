from __future__ import annotations

from collections import Counter

from travel_agent.domain.critique_models import (
    CandidateEvidenceDigest,
    SoftCritique,
    SoftDimension,
    SuggestedActionKind,
)


def validate_critique_grounding(
    digests: tuple[CandidateEvidenceDigest, ...],
    critiques: tuple[SoftCritique, ...],
) -> tuple[str, ...]:
    """校验引用和动作边界；不声称证明自然语言语义蕴含。"""
    errors: list[str] = []
    requested_ids = [digest.candidate_id for digest in digests]
    returned_ids = [critique.candidate_id for critique in critiques]
    if Counter(returned_ids) != Counter(requested_ids):
        errors.append("candidate_set_mismatch")

    digest_by_id = {digest.candidate_id: digest for digest in digests}
    expected_dimensions = set(SoftDimension)
    for critique in critiques:
        digest = digest_by_id.get(critique.candidate_id)
        if digest is None:
            errors.append(f"unknown_candidate:{critique.candidate_id}")
            continue
        evidence_by_id = {item.id: item for item in digest.evidence}
        dimensions = [item.dimension for item in critique.dimensions]
        if set(dimensions) != expected_dimensions or len(dimensions) != len(
            expected_dimensions
        ):
            errors.append(f"dimension_set_invalid:{critique.candidate_id}")
        for count_dimension, count in Counter(dimensions).items():
            if count != 1:
                errors.append(
                    f"duplicate_dimension:{critique.candidate_id}:{count_dimension.value}"
                )
        for dimension in critique.dimensions:
            if not dimension.evidence_ids:
                errors.append(
                    f"missing_evidence:{critique.candidate_id}:{dimension.dimension.value}"
                )
            for evidence_id in dimension.evidence_ids:
                if evidence_id not in evidence_by_id:
                    errors.append(
                        f"unknown_evidence:{critique.candidate_id}:{evidence_id}"
                    )
            action = dimension.suggested_action
            if action is None or action.kind is SuggestedActionKind.NO_ACTION:
                continue
            if not action.evidence_ids:
                errors.append(
                    f"action_missing_evidence:{critique.candidate_id}:{dimension.dimension.value}"
                )
            for evidence_id in action.evidence_ids:
                if evidence_id not in evidence_by_id:
                    errors.append(
                        f"action_unknown_evidence:{critique.candidate_id}:{evidence_id}"
                    )
            entity_evidence = [
                item
                for item in digest.evidence
                if item.entity_id == action.poi_id
            ]
            if not entity_evidence:
                errors.append(
                    f"action_unknown_poi:{critique.candidate_id}:{action.poi_id}"
                )
            if action.from_day is not None and not any(
                item.day == action.from_day and item.entity_id == action.poi_id
                for item in digest.evidence
            ):
                errors.append(
                    f"action_invalid_from_day:{critique.candidate_id}:{action.poi_id}"
                )
            if (
                action.kind is SuggestedActionKind.MOVE_OPTIONAL_POI
                and action.to_day is not None
                and not any(item.day == action.to_day for item in digest.evidence)
            ):
                errors.append(
                    f"action_invalid_to_day:{critique.candidate_id}:{action.poi_id}"
                )
            if action.kind is SuggestedActionKind.REMOVE_OPTIONAL_POI and any(
                item.entity_id == action.poi_id
                and item.field == "must_visit"
                and item.value is True
                for item in digest.evidence
            ):
                errors.append(
                    f"action_removes_must_visit:{critique.candidate_id}:{action.poi_id}"
                )
        for evidence_id in critique.tradeoff_evidence_ids:
            if evidence_id not in evidence_by_id:
                errors.append(
                    f"tradeoff_unknown_evidence:{critique.candidate_id}:{evidence_id}"
                )
    return tuple(dict.fromkeys(errors))
