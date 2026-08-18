from __future__ import annotations

from decimal import Decimal

from travel_agent.domain.models import PlanningRequest
from travel_agent.graph.workflow import run_planning


def test_workflow_builds_valid_plan(hangzhou_trip):
    response = run_planning(
        PlanningRequest(trip=hangzhou_trip, max_replan_rounds=2),
        thread_id="test-valid-plan",
    )

    assert response.status == "completed"
    assert response.selected_plan is not None
    assert response.selected_plan.validation is not None
    assert response.selected_plan.validation.valid
    scheduled_names = {
        item.name
        for day in response.selected_plan.days
        for item in day.items
    }
    assert "灵隐寺" in scheduled_names


def test_workflow_returns_infeasible_when_required_poi_exceeds_budget(hangzhou_trip):
    constrained = hangzhou_trip.model_copy(
        update={"total_budget": Decimal("10")}
    )
    response = run_planning(
        PlanningRequest(trip=constrained, max_replan_rounds=1),
        thread_id="test-infeasible-plan",
    )

    assert response.status == "infeasible"
    assert response.selected_plan is None
    assert response.iterations == 1

