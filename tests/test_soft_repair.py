from __future__ import annotations

from datetime import date

import pytest

from travel_agent.domain.critique_models import (
    DimensionCritique,
    SoftCritique,
    SoftDimension,
    SoftRepairPlan,
    SuggestedActionKind,
    SuggestedSoftAction,
)
from travel_agent.domain.models import PlanStyle, PlanningRequest
from travel_agent.domain.repair_models import RepairAction, RepairActionKind
from travel_agent.planning.drafts import CandidateDraft, DraftDay
from travel_agent.graph.workflow import run_planning
from travel_agent.planning.soft_repair import (
    apply_soft_repair,
    compile_soft_repair_plan,
)


DAY_1 = date(2026, 10, 2)
DAY_2 = date(2026, 10, 3)


def _draft() -> CandidateDraft:
    return CandidateDraft(
        id="candidate",
        style=PlanStyle.BALANCED,
        days=(
            DraftDay(date=DAY_1, poi_ids=("poi-1", "poi-2")),
            DraftDay(date=DAY_2, poi_ids=("poi-3",)),
        ),
    )


def _plan(kind: RepairActionKind, *, poi_id="poi-2", from_day=DAY_1, to_day=None):
    action = RepairAction(
        kind=kind,
        source_violation_type="soft:pace",
        poi_id=poi_id,
        from_day=from_day,
        to_day=to_day,
        reason="测试",
        expected_effect="改善",
    )
    return SoftRepairPlan(
        round=1,
        target_candidate_id="candidate",
        source_dimension=SoftDimension.PACE,
        source_evidence_ids=("ev_12345678",),
        action=action,
        affected_days=(DAY_1,),
        preserved_days=(DAY_2,),
        action_fingerprint="fingerprint",
    )


def test_apply_soft_repair_supports_remove_move_and_reorder():
    removed = apply_soft_repair(
        _draft(), _plan(RepairActionKind.REMOVE_OPTIONAL_POI)
    )
    assert removed.days[0].poi_ids == ("poi-1",)

    moved = apply_soft_repair(
        _draft(), _plan(RepairActionKind.MOVE_POI, to_day=DAY_2)
    )
    assert moved.days[0].poi_ids == ("poi-1",)
    assert moved.days[1].poi_ids == ("poi-3", "poi-2")

    reordered = apply_soft_repair(
        _draft(), _plan(RepairActionKind.REORDER_OPTIONAL_POI)
    )
    assert reordered.days[0].poi_ids == ("poi-2", "poi-1")


@pytest.mark.parametrize(
    "plan",
    [
        _plan(RepairActionKind.REMOVE_OPTIONAL_POI, poi_id="missing"),
        _plan(RepairActionKind.MOVE_POI, to_day=date(2099, 1, 1)),
        _plan(RepairActionKind.INSERT_MUST_VISIT, to_day=DAY_2),
    ],
)
def test_apply_soft_repair_rejects_invalid_or_unsupported_actions(plan):
    with pytest.raises(ValueError):
        apply_soft_repair(_draft(), plan)


def _critique(candidate_id: str, action: SuggestedSoftAction | None):
    return SoftCritique(
        candidate_id=candidate_id,
        dimensions=(
            DimensionCritique(
                dimension=SoftDimension.PACE,
                score=40,
                summary="需要调整",
                evidence_ids=("ev_12345678",),
                suggested_action=action,
            ),
        ),
        overall_summary="测试",
    )


@pytest.mark.asyncio
async def test_soft_repair_compiler_enforces_context_entity_and_must_visit(
    hangzhou_trip, mock_workflow
):
    response = await run_planning(
        mock_workflow,
        PlanningRequest(trip=hangzhou_trip, max_replan_rounds=0),
        thread_id="soft-compiler-boundaries",
    )
    snapshot = await mock_workflow.aget_state(
        {"configurable": {"thread_id": "soft-compiler-boundaries"}}
    )
    state = snapshot.values
    candidate = response.candidates[0]
    draft = next(item for item in state["candidate_drafts"] if item.id == candidate.id)

    plan, reason = compile_soft_repair_plan(
        hangzhou_trip,
        candidate,
        draft,
        state["planning_pois"],
        _critique("wrong-candidate", None),
        repair_round=1,
    )
    assert plan is None and reason == "soft_context_mismatch"

    plan, reason = compile_soft_repair_plan(
        hangzhou_trip,
        candidate,
        draft,
        state["planning_pois"],
        _critique(candidate.id, None),
        repair_round=1,
    )
    assert plan is None and reason == "no_safe_soft_action"

    must_item = next(
        item
        for day in candidate.days
        for item in day.items
        if item.poi_id is not None and "灵隐寺" in item.name
    )
    must_day = next(
        day.date for day in candidate.days if must_item in day.items
    )
    remove_must = SuggestedSoftAction(
        kind=SuggestedActionKind.REMOVE_OPTIONAL_POI,
        poi_id=must_item.poi_id,
        from_day=must_day,
        evidence_ids=("ev_12345678",),
        expected_dimension=SoftDimension.PACE,
    )
    plan, reason = compile_soft_repair_plan(
        hangzhou_trip,
        candidate,
        draft,
        state["planning_pois"],
        _critique(candidate.id, remove_must),
        repair_round=1,
    )
    assert plan is None and reason == "no_safe_soft_action"

    unknown = SuggestedSoftAction(
        kind=SuggestedActionKind.REMOVE_OPTIONAL_POI,
        poi_id="unknown",
        from_day=must_day,
        evidence_ids=("ev_12345678",),
        expected_dimension=SoftDimension.PACE,
    )
    plan, reason = compile_soft_repair_plan(
        hangzhou_trip,
        candidate,
        draft,
        state["planning_pois"],
        _critique(candidate.id, unknown),
        repair_round=1,
    )
    assert plan is None and reason == "no_safe_soft_action"


@pytest.mark.asyncio
async def test_soft_repair_compiler_can_compile_safe_reorder(
    hangzhou_trip, mock_workflow
):
    response = await run_planning(
        mock_workflow,
        PlanningRequest(trip=hangzhou_trip, max_replan_rounds=0),
        thread_id="soft-compiler-reorder",
    )
    snapshot = await mock_workflow.aget_state(
        {"configurable": {"thread_id": "soft-compiler-reorder"}}
    )
    state = snapshot.values
    candidate = response.candidates[0]
    draft = next(item for item in state["candidate_drafts"] if item.id == candidate.id)
    poi_by_id = {poi.facts.id: poi for poi in state["planning_pois"]}
    source_day = next(day for day in draft.days if len(day.poi_ids) >= 2)
    optional_id = next(
        poi_id
        for poi_id in source_day.poi_ids
        if "灵隐寺" not in poi_by_id[poi_id].facts.name
    )
    action = SuggestedSoftAction(
        kind=SuggestedActionKind.REORDER_OPTIONAL_POI,
        poi_id=optional_id,
        from_day=source_day.date,
        to_day=source_day.date,
        evidence_ids=("ev_12345678",),
        expected_dimension=SoftDimension.GEOGRAPHIC_COHERENCE,
    )
    plan, reason = compile_soft_repair_plan(
        hangzhou_trip,
        candidate,
        draft,
        state["planning_pois"],
        _critique(candidate.id, action),
        repair_round=1,
    )
    assert reason is None
    assert plan is not None
    assert plan.action.kind is RepairActionKind.REORDER_OPTIONAL_POI

    target_day = next(
        day
        for day in draft.days
        if day.date != source_day.date
        and sum(poi_by_id[item].duration_minutes for item in day.poi_ids)
        + poi_by_id[optional_id].duration_minutes
        <= hangzhou_trip.mobility.max_daily_activity_minutes
    )
    move = SuggestedSoftAction(
        kind=SuggestedActionKind.MOVE_OPTIONAL_POI,
        poi_id=optional_id,
        from_day=source_day.date,
        to_day=target_day.date,
        evidence_ids=("ev_12345678",),
        expected_dimension=SoftDimension.PACE,
    )
    move_plan, reason = compile_soft_repair_plan(
        hangzhou_trip,
        candidate,
        draft,
        state["planning_pois"],
        _critique(candidate.id, move),
        repair_round=1,
    )
    assert reason is None
    assert move_plan is not None
    assert move_plan.action.kind is RepairActionKind.MOVE_POI

    invalid_target = move.model_copy(update={"to_day": date(2099, 1, 1)})
    plan, reason = compile_soft_repair_plan(
        hangzhou_trip,
        candidate,
        draft,
        state["planning_pois"],
        _critique(candidate.id, invalid_target),
        repair_round=1,
    )
    assert plan is None and reason == "no_safe_soft_action"

    constrained_trip = hangzhou_trip.model_copy(
        update={
            "mobility": hangzhou_trip.mobility.model_copy(
                update={"max_daily_activity_minutes": 1}
            )
        }
    )
    plan, reason = compile_soft_repair_plan(
        constrained_trip,
        candidate,
        draft,
        state["planning_pois"],
        _critique(candidate.id, move),
        repair_round=1,
    )
    assert plan is None and reason == "no_safe_soft_action"
