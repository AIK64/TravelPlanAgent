from __future__ import annotations

import logging
from decimal import Decimal

import pytest

from travel_agent.domain.models import (
    PlanningRequest,
    ValidationResult,
    ValidationStatus,
    Violation,
    ViolationSeverity,
)
from travel_agent.domain.repair_models import RepairOutcome
from travel_agent.graph.workflow import run_planning
from travel_agent.planning.impact import day_fingerprint


@pytest.mark.asyncio
async def test_budget_violation_is_repaired_locally_and_preserves_other_days(
    hangzhou_trip,
    fallback_workflow_harness,
    caplog,
):
    caplog.set_level(logging.INFO)
    thread_id = "local-repair-budget"
    trip = hangzhou_trip.model_copy(update={"total_budget": Decimal("300")})

    response = await run_planning(
        fallback_workflow_harness.workflow,
        PlanningRequest(trip=trip, max_replan_rounds=2),
        thread_id=thread_id,
    )

    assert response.status == "completed"
    assert response.iterations == 1
    assert response.selected_plan is not None
    assert response.selected_plan.validation is not None
    assert response.selected_plan.validation.status in {
        ValidationStatus.VALID,
        ValidationStatus.VALID_WITH_WARNINGS,
    }
    assert response.selected_plan.metrics.known_estimated_cost <= Decimal("300")

    snapshot = await fallback_workflow_harness.workflow.aget_state(
        {"configurable": {"thread_id": thread_id}}
    )
    state = snapshot.values
    assert len(state["repair_history"]) == 1
    attempt = state["repair_history"][0]
    assert attempt.outcome is RepairOutcome.RESOLVED
    assert attempt.after_error_count == 0
    assert attempt.preserved_day_count == 2
    assert attempt.reused_route_count > 0
    selected_days = {
        day.date.isoformat(): day_fingerprint(day)
        for day in response.selected_plan.days
    }
    assert all(
        selected_days[day] == fingerprint
        for day, fingerprint in state["preserved_day_hashes"].items()
    )

    events = [
        record.getMessage().split(maxsplit=1)[0]
        for record in caplog.records
        if f"thread_id={thread_id}" in record.getMessage()
    ]
    assert {
        "critic.completed",
        "repair.plan.created",
        "repair.action.applied",
        "repair.routes.reused",
        "repair.validation.delta",
        "repair.terminated",
    } <= set(events)


@pytest.mark.asyncio
async def test_required_poi_over_budget_terminates_without_unsafe_repair(
    hangzhou_trip,
    fallback_workflow_harness,
):
    thread_id = "local-repair-hard-budget"
    trip = hangzhou_trip.model_copy(update={"total_budget": Decimal("10")})

    response = await run_planning(
        fallback_workflow_harness.workflow,
        PlanningRequest(trip=trip, max_replan_rounds=5),
        thread_id=thread_id,
    )

    assert response.status == "infeasible"
    assert response.iterations == 0
    snapshot = await fallback_workflow_harness.workflow.aget_state(
        {"configurable": {"thread_id": thread_id}}
    )
    state = snapshot.values
    assert state["repair_history"] == []
    assert state["repair_plan"] is None
    assert state["repair_terminal_reason"] == "hard_constraint_conflict:budget"


def test_graph_exposes_explicit_critic_local_repair_and_delta_tool_nodes(
    mock_workflow,
):
    graph = mock_workflow.get_graph()
    node_names = set(graph.nodes)
    edges = {(edge.source, edge.target) for edge in graph.edges}

    assert {
        "select_repair_target",
        "analyze_violations",
        "build_repair_plan",
        "apply_local_repair",
        "collect_delta_routes",
        "load_delta_routes",
    } <= node_names
    assert {
        ("select_repair_target", "analyze_violations"),
        ("apply_local_repair", "collect_delta_routes"),
        ("load_delta_routes", "materialize_candidates"),
    } <= edges
    assert "replan" not in node_names


@pytest.mark.asyncio
async def test_repeated_violation_fingerprint_stops_repair_loop(
    hangzhou_trip,
    fallback_workflow_harness,
    monkeypatch,
):
    from travel_agent.graph import workflow as workflow_module

    original_validate = workflow_module.validate_candidate

    def sticky_budget_violation(trip, candidate, pois):
        if "repair-r1" in candidate.id:
            return ValidationResult.from_violations(
                [
                    Violation(
                        type="budget_exceeded",
                        severity=ViolationSeverity.ERROR,
                        message="测试用重复违规",
                        repair_hint="不应继续重复相同修复",
                    ),
                    Violation(
                        type="walking_distance_estimated",
                        severity=ViolationSeverity.WARNING,
                        message="沿用初始候选的步行估算告警",
                    ),
                ]
            )
        return original_validate(trip, candidate, pois)

    monkeypatch.setattr(workflow_module, "validate_candidate", sticky_budget_violation)
    thread_id = "local-repair-repeated-fingerprint"
    trip = hangzhou_trip.model_copy(update={"total_budget": Decimal("300")})

    response = await run_planning(
        fallback_workflow_harness.workflow,
        PlanningRequest(trip=trip, max_replan_rounds=5),
        thread_id=thread_id,
    )

    assert response.status == "infeasible"
    assert response.iterations == 1
    snapshot = await fallback_workflow_harness.workflow.aget_state(
        {"configurable": {"thread_id": thread_id}}
    )
    state = snapshot.values
    assert len(state["repair_history"]) == 1
    assert state["repair_history"][0].outcome is RepairOutcome.NO_PROGRESS
    assert state["repair_terminal_reason"] == "repeated_violation_fingerprint"
