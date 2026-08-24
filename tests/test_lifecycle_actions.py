from datetime import timedelta
from uuid import uuid4

import pytest

from travel_agent.domain.lifecycle_models import (
    EditOperation,
    EditOperationKind,
    EditPatch,
    LockKind,
    PlanLock,
)
from travel_agent.domain.models import PlanningRequest
from travel_agent.graph.workflow import run_planning
from travel_agent.lifecycle.actions import apply_edit_patch, ground_edit_patch
from travel_agent.lifecycle.errors import LifecycleActionError
from travel_agent.lifecycle.fingerprints import item_fingerprint, with_stable_item_ids
from travel_agent.lifecycle.impact import analyze_change_impact


async def _context(workflow, trip):
    thread_id = f"actions-{uuid4()}"
    response = await run_planning(
        workflow, PlanningRequest(trip=trip), thread_id=thread_id
    )
    state = await workflow.aget_state({"configurable": {"thread_id": thread_id}})
    candidate = with_stable_item_ids("session", response.selected_plan)
    draft = next(item for item in state.values["candidate_drafts"] if item.id == candidate.id)
    return candidate, draft, tuple(state.values["planning_pois"])


@pytest.mark.asyncio
async def test_ground_and_apply_move_reorder_remove_add_replace(
    mock_workflow, hangzhou_trip
):
    candidate, draft, pois = await _context(mock_workflow, hangzhou_trip)
    source = next(
        item
        for day in candidate.days
        for item in day.items
        if "灵隐寺" not in item.name
    )
    source_day = next(day.date for day in candidate.days if source in day.items)
    target_day = next(day.date for day in candidate.days if day.date != source_day)

    grounded = ground_edit_patch(
        "session",
        candidate,
        EditPatch(
            operations=(
                EditOperation(
                    kind=EditOperationKind.MOVE_ITEM,
                    item_name=source.name,
                    target_date=target_day,
                ),
            )
        ),
    )
    moved, ids = apply_edit_patch(
        session_id="session",
        request_id="move",
        trip=hangzhou_trip,
        candidate=candidate,
        draft=draft,
        planning_pois=pois,
        patch=grounded,
    )
    assert source.poi_id not in next(day.poi_ids for day in moved.days if day.date == source_day)
    assert ids[source.poi_id] == source.item_id

    reordered, _ = apply_edit_patch(
        session_id="session",
        request_id="reorder",
        trip=hangzhou_trip,
        candidate=candidate,
        draft=draft,
        planning_pois=pois,
        patch=EditPatch(
            operations=(
                EditOperation(
                    kind=EditOperationKind.REORDER_ITEM,
                    item_id=source.item_id,
                    target_index=0,
                ),
            )
        ),
    )
    assert next(day.poi_ids for day in reordered.days if day.date == source_day)[0] == source.poi_id

    removed, _ = apply_edit_patch(
        session_id="session",
        request_id="remove",
        trip=hangzhou_trip,
        candidate=candidate,
        draft=draft,
        planning_pois=pois,
        patch=EditPatch(
            operations=(
                EditOperation(kind=EditOperationKind.REMOVE_ITEM, item_id=source.item_id),
            )
        ),
    )
    assert source.poi_id not in {poi for day in removed.days for poi in day.poi_ids}

    replacement = next(poi for poi in pois if poi.facts.id != source.poi_id)
    replaced, replacement_ids = apply_edit_patch(
        session_id="session",
        request_id="replace",
        trip=hangzhou_trip,
        candidate=candidate,
        draft=draft,
        planning_pois=pois,
        patch=EditPatch(
            operations=(
                EditOperation(
                    kind=EditOperationKind.REPLACE_ITEM,
                    item_id=source.item_id,
                    poi_name=replacement.facts.name,
                ),
            )
        ),
    )
    assert replacement.facts.id in {poi for day in replaced.days for poi in day.poi_ids}
    assert replacement_ids[replacement.facts.id].startswith("item_")

    added, added_ids = apply_edit_patch(
        session_id="session",
        request_id="add",
        trip=hangzhou_trip,
        candidate=candidate,
        draft=draft,
        planning_pois=pois,
        patch=EditPatch(
            operations=(
                EditOperation(
                    kind=EditOperationKind.ADD_ITEM,
                    target_date=target_day,
                    poi_name=replacement.facts.name,
                ),
            )
        ),
    )
    assert len(next(day.poi_ids for day in added.days if day.date == target_day)) > len(
        next(day.poi_ids for day in draft.days if day.date == target_day)
    )
    assert added_ids[replacement.facts.id].startswith("item_")


@pytest.mark.asyncio
async def test_action_guards_must_visit_unknown_and_out_of_range(
    mock_workflow, hangzhou_trip
):
    candidate, draft, pois = await _context(mock_workflow, hangzhou_trip)
    must = next(item for day in candidate.days for item in day.items if "灵隐寺" in item.name)

    with pytest.raises(LifecycleActionError, match="必去"):
        apply_edit_patch(
            session_id="session",
            request_id="must",
            trip=hangzhou_trip,
            candidate=candidate,
            draft=draft,
            planning_pois=pois,
            patch=EditPatch(
                operations=(
                    EditOperation(kind=EditOperationKind.REMOVE_ITEM, item_id=must.item_id),
                )
            ),
        )
    with pytest.raises(LifecycleActionError, match="不在旅行范围"):
        apply_edit_patch(
            session_id="session",
            request_id="date",
            trip=hangzhou_trip,
            candidate=candidate,
            draft=draft,
            planning_pois=pois,
            patch=EditPatch(
                operations=(
                    EditOperation(
                        kind=EditOperationKind.MOVE_ITEM,
                        item_id=must.item_id,
                        target_date=hangzhou_trip.end_date + timedelta(days=1),
                    ),
                )
            ),
        )
    with pytest.raises(LifecycleActionError, match="不存在"):
        ground_edit_patch(
            "session",
            candidate,
            EditPatch(
                operations=(
                    EditOperation(
                        kind=EditOperationKind.REMOVE_ITEM, item_id="unknown"
                    ),
                )
            ),
        )


@pytest.mark.asyncio
async def test_impact_scope_and_item_lock_are_deterministic(
    mock_workflow, hangzhou_trip
):
    candidate, _draft, _pois = await _context(mock_workflow, hangzhou_trip)
    item = candidate.days[0].items[0]
    lock = PlanLock(
        lock_id=f"item:{item.item_id}",
        kind=LockKind.ITEM,
        target_id=item.item_id,
        expected_fingerprint=item_fingerprint(item, candidate.days[0].date),
        created_by_request_id="lock",
    )
    impact = analyze_change_impact(
        candidate,
        EditPatch(
            operations=(
                EditOperation(
                    kind=EditOperationKind.MOVE_ITEM,
                    item_id=item.item_id,
                    target_date=candidate.days[1].date,
                ),
            )
        ),
        (lock,),
    )
    assert impact.scope.value == "multi_day"
    assert impact.lock_conflicts == (lock.lock_id,)
    assert impact.required_tool_operations == ("route.delta",)

