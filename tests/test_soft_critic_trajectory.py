from __future__ import annotations

import pytest

from travel_agent.critique.errors import CriticErrorCategory, CriticProviderError
from travel_agent.critique.gateway import CriticGateway
from travel_agent.critique.explanation import build_grounded_explanation
from travel_agent.critique.quality import CriticPolicy
from travel_agent.domain.critique_models import (
    CriticStatus,
    DimensionCritique,
    SoftCritique,
    SoftCriticProviderOutput,
    SoftDimension,
    SuggestedActionKind,
    SuggestedSoftAction,
)
from travel_agent.domain.models import PlanningRequest
from travel_agent.domain.tool_models import UnknownFactPolicy
from travel_agent.graph.workflow import build_workflow, run_planning
from travel_agent.planning.defaults import POIDefaultPolicy


pytestmark = pytest.mark.asyncio


class InvalidGroundingModel:
    name = "invalid-fixture"
    model = "invalid-v1"
    prompt_version = "test-v1"

    def __init__(self) -> None:
        self.calls = 0

    async def critique(self, request):
        self.calls += 1
        return SoftCriticProviderOutput(
            critiques=tuple(
                _scored_critique(digest, 80, evidence_id="ev_unknown")
                for digest in request.digests
            )
        )


class UnavailableModel:
    name = "unavailable-fixture"
    model = "unavailable-v1"
    prompt_version = "test-v1"

    async def critique(self, _request):
        raise CriticProviderError(
            CriticErrorCategory.TIMEOUT,
            "timeout",
            True,
            "测试超时",
        )


class ImprovingSoftRepairModel:
    name = "repair-fixture"
    model = "repair-v1"
    prompt_version = "test-v1"

    async def critique(self, request):
        return SoftCriticProviderOutput(
            critiques=tuple(
                _repair_critique(digest)
                if "-soft-r" not in digest.candidate_id
                else _scored_critique(digest, 90)
                for digest in request.digests
            )
        )


def _scored_critique(digest, score: int, evidence_id: str | None = None):
    reference = evidence_id or digest.evidence[0].id
    return SoftCritique(
        candidate_id=digest.candidate_id,
        dimensions=tuple(
            DimensionCritique(
                dimension=dimension,
                score=score,
                summary=f"{dimension.value}={score}",
                evidence_ids=(reference,),
            )
            for dimension in SoftDimension
        ),
        overall_summary="测试评价",
        tradeoff_evidence_ids=(reference,),
    )


def _repair_critique(digest):
    optional = next(
        (
            item
            for item in digest.evidence
            if item.field == "must_visit"
            and item.value is False
            and item.day is not None
        ),
        None,
    )
    reference = optional.id if optional is not None else digest.evidence[0].id
    dimensions = []
    for dimension in SoftDimension:
        action = None
        if dimension is SoftDimension.PACE and optional is not None:
            action = SuggestedSoftAction(
                kind=SuggestedActionKind.REMOVE_OPTIONAL_POI,
                poi_id=optional.entity_id,
                from_day=optional.day,
                evidence_ids=(reference,),
                expected_dimension=dimension,
            )
        dimensions.append(
            DimensionCritique(
                dimension=dimension,
                score=50,
                summary="降低密度以改善节奏",
                evidence_ids=(reference,),
                suggested_action=action,
            )
        )
    return SoftCritique(
        candidate_id=digest.candidate_id,
        dimensions=tuple(dimensions),
        overall_summary="当前方案偏密",
        tradeoff_evidence_ids=(reference,),
    )


def _workflow(gateway_factory, model, *, max_attempts=1):
    gateway = gateway_factory()
    critic_gateway = CriticGateway(
        model=model,
        timeout_seconds=1,
        max_attempts=max_attempts,
        base_delay_seconds=0,
        max_delay_seconds=0,
    )
    return build_workflow(
        gateway,
        POIDefaultPolicy(UnknownFactPolicy.ASSUME_WITH_WARNING),
        critic_gateway=critic_gateway,
        critic_policy=CriticPolicy(),
    )


async def test_invalid_grounding_retries_once_then_degrades(
    hangzhou_trip, gateway_factory
):
    model = InvalidGroundingModel()
    workflow = _workflow(gateway_factory, model)
    response = await run_planning(
        workflow,
        PlanningRequest(trip=hangzhou_trip, max_replan_rounds=0),
        thread_id="critic-invalid-grounding",
    )
    assert response.status == "completed"
    assert response.critic_status is CriticStatus.INVALID_GROUNDING
    assert response.selected_plan is not None
    assert model.calls == 2


async def test_critic_failure_does_not_become_infeasible(
    hangzhou_trip, gateway_factory
):
    workflow = _workflow(gateway_factory, UnavailableModel(), max_attempts=2)
    response = await run_planning(
        workflow,
        PlanningRequest(trip=hangzhou_trip, max_replan_rounds=0),
        thread_id="critic-unavailable",
    )
    assert response.status == "completed"
    assert response.critic_status is CriticStatus.UNAVAILABLE
    assert response.selected_plan is not None
    assert response.candidate_critiques == []
    with pytest.raises(ValueError, match="evidence digest"):
        build_grounded_explanation(
            response.selected_plan,
            status=CriticStatus.UNAVAILABLE,
            digest=None,
            critique=None,
        )


async def test_one_soft_repair_returns_to_hard_validation_and_is_accepted(
    hangzhou_trip, gateway_factory
):
    workflow = _workflow(gateway_factory, ImprovingSoftRepairModel())
    response = await run_planning(
        workflow,
        PlanningRequest(trip=hangzhou_trip, max_replan_rounds=0),
        thread_id="critic-soft-repair",
    )
    assert response.status == "completed"
    assert response.critic_status is CriticStatus.SUCCESS
    assert response.soft_iterations == 1
    assert response.selected_plan is not None
    assert "-soft-r1" in response.selected_plan.id
    snapshot = await workflow.aget_state(
        {"configurable": {"thread_id": "critic-soft-repair"}}
    )
    assert snapshot.values["soft_repair_history"][0].accepted is True
