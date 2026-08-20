from __future__ import annotations

import logging
from decimal import Decimal

import pytest

from travel_agent.app import create_app
from travel_agent.domain.models import PlanningRequest
from travel_agent.graph.workflow import run_planning


def test_create_app_applies_configured_log_level(monkeypatch):
    app_logger = logging.getLogger("travel_agent")
    previous_level = app_logger.level
    monkeypatch.setenv("APP_LOG_LEVEL", "DEBUG")

    try:
        create_app()
        assert app_logger.level == logging.DEBUG
    finally:
        app_logger.setLevel(previous_level)


@pytest.mark.asyncio
async def test_info_logs_show_planning_flow(caplog, hangzhou_trip, mock_workflow):
    caplog.set_level(logging.INFO)

    await run_planning(
        mock_workflow,
        PlanningRequest(trip=hangzhou_trip, max_replan_rounds=2),
        thread_id="log-flow-test",
    )

    messages = [record.getMessage() for record in caplog.records]
    expected_events = {
        "planning.started",
        "node.started",
        "search_plan.created",
        "poi_context.loaded",
        "candidate_drafts.prepared",
        "routes.loaded",
        "candidate.generated",
        "candidate.validated",
        "routing.decision",
        "planning.completed",
    }

    for event in expected_events:
        assert any(message.startswith(event) for message in messages), event

    flow_messages = [
        message
        for message in messages
        if any(message.startswith(event) for event in expected_events)
    ]
    assert flow_messages
    assert all("thread_id=log-flow-test" in message for message in flow_messages)


@pytest.mark.asyncio
async def test_debug_logs_include_candidate_day_summaries(
    caplog,
    hangzhou_trip,
    mock_workflow,
):
    caplog.set_level(logging.DEBUG, logger="travel_agent.graph.workflow")

    await run_planning(
        mock_workflow,
        PlanningRequest(trip=hangzhou_trip, max_replan_rounds=2),
        thread_id="log-debug-test",
    )

    schedule_messages = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("candidate.schedule")
    ]
    assert schedule_messages
    assert all("thread_id=log-debug-test" in message for message in schedule_messages)
    assert any("day=2026-10-02" in message for message in schedule_messages)
    assert any("poi_names=" in message for message in schedule_messages)


@pytest.mark.asyncio
async def test_info_logs_show_replan_to_infeasible_flow(
    caplog,
    hangzhou_trip,
    mock_workflow,
):
    caplog.set_level(logging.INFO, logger="travel_agent.graph.workflow")
    constrained_trip = hangzhou_trip.model_copy(
        update={"total_budget": Decimal("10")}
    )

    await run_planning(
        mock_workflow,
        PlanningRequest(trip=constrained_trip, max_replan_rounds=1),
        thread_id="log-replan-test",
    )

    messages = [record.getMessage() for record in caplog.records]
    assert any(
        message.startswith("routing.decision") and "next=replan" in message
        for message in messages
    )
    assert any(message.startswith("replan.round_started") for message in messages)
    assert any(message.startswith("replan.completed") for message in messages)
    assert any(
        message.startswith("routing.decision") and "next=mark_infeasible" in message
        for message in messages
    )
    assert any(message.startswith("planning.infeasible") for message in messages)
    assert any(
        message.startswith("planning.completed") and "status=infeasible" in message
        for message in messages
    )
