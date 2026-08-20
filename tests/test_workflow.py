from __future__ import annotations

from decimal import Decimal

import pytest

from travel_agent.domain.models import (
    PlanningRequest,
    ValidationResult,
    ValidationStatus,
    Violation,
    ViolationSeverity,
)
from travel_agent.graph.workflow import run_planning, select_best_candidate


@pytest.mark.asyncio
async def test_workflow_builds_valid_plan(hangzhou_trip, mock_workflow):
    response = await run_planning(
        mock_workflow,
        PlanningRequest(trip=hangzhou_trip, max_replan_rounds=2),
        thread_id="test-valid-plan",
    )

    assert response.status == "completed"
    assert response.selected_plan is not None
    assert response.selected_plan.validation is not None
    assert response.selected_plan.validation.valid
    assert (
        response.selected_plan.validation.status
        is ValidationStatus.VALID_WITH_WARNINGS
    )
    scheduled_names = {
        item.name
        for day in response.selected_plan.days
        for item in day.items
    }
    assert "灵隐寺" in scheduled_names


@pytest.mark.asyncio
async def test_workflow_returns_infeasible_when_required_poi_exceeds_budget(
    hangzhou_trip,
    mock_workflow,
):
    constrained = hangzhou_trip.model_copy(
        update={"total_budget": Decimal("10")}
    )
    response = await run_planning(
        mock_workflow,
        PlanningRequest(trip=constrained, max_replan_rounds=1),
        thread_id="test-infeasible-plan",
    )

    assert response.status == "infeasible"
    assert response.selected_plan is None
    assert response.iterations == 1


@pytest.mark.asyncio
async def test_fully_valid_candidate_ranks_ahead_of_higher_scored_warning_candidate(
    hangzhou_trip,
    mock_workflow,
):
    response = await run_planning(
        mock_workflow,
        PlanningRequest(trip=hangzhou_trip, max_replan_rounds=0),
        thread_id="test-validation-rank",
    )
    base = response.candidates[0]
    warning_candidate = base.model_copy(
        update={
            "id": "warning-high-score",
            "score": 1.0,
            "validation": ValidationResult.from_violations(
                [
                    Violation(
                        type="unverified",
                        severity=ViolationSeverity.WARNING,
                        message="事实待确认",
                    )
                ]
            ),
        }
    )
    valid_candidate = base.model_copy(
        update={
            "id": "valid-low-score",
            "score": -1.0,
            "validation": ValidationResult.from_violations([]),
        }
    )

    selected = select_best_candidate([warning_candidate, valid_candidate])

    assert selected.id == "valid-low-score"


def test_graph_exposes_tool_use_and_replan_nodes(mock_workflow):
    graph = mock_workflow.get_graph()
    node_names = set(graph.nodes)
    edges = {(edge.source, edge.target) for edge in graph.edges}

    assert {
        "build_search_plan",
        "load_pois",
        "resolve_poi_facts",
        "prepare_candidate_drafts",
        "load_routes",
        "materialize_candidates",
        "validate_candidates",
        "replan",
        "select_best",
        "mark_infeasible",
    } <= node_names
    assert {
        ("__start__", "build_search_plan"),
        ("build_search_plan", "load_pois"),
        ("load_pois", "resolve_poi_facts"),
        ("resolve_poi_facts", "prepare_candidate_drafts"),
        ("prepare_candidate_drafts", "load_routes"),
        ("load_routes", "materialize_candidates"),
        ("materialize_candidates", "validate_candidates"),
        ("replan", "prepare_candidate_drafts"),
        ("select_best", "__end__"),
        ("mark_infeasible", "__end__"),
    } <= edges
    assert ("replan", "validate_candidates") not in edges

