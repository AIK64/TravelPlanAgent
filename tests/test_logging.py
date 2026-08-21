from __future__ import annotations

import logging
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from travel_agent.app import create_app
from travel_agent.config import Settings
from travel_agent.domain.models import PlanningRequest
from travel_agent.domain.tool_models import UnknownFactPolicy
from travel_agent.graph.workflow import build_workflow, run_planning
from travel_agent.planning.defaults import POIDefaultPolicy
from travel_agent.runtime import PlanningRuntime
from travel_agent.tools.gateway import build_gateway
from travel_agent.tools.providers.amap import (
    AMapClient,
    AMapPOIProvider,
    AMapRouteProvider,
)


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


def test_amap_failure_keeps_503_logs_traceback_and_checkpoint_safe(
    monkeypatch,
    caplog,
    hangzhou_trip,
):
    """底层 httpx 异常不得经 Agent、API 日志或 checkpoint 重新暴露供应商秘密。"""
    secret = "amap-super-secret-test-key"
    raw_payload = "RAW_AMAP_SUPPLIER_PAYLOAD"
    thread_id = "amap-safe-503"
    calls = 0
    runtime_box: dict[str, PlanningRuntime] = {}
    settings = Settings.from_env(
        {
            "TRAVEL_PROVIDER": "amap",
            "AMAP_API_KEY": secret,
            "TOOL_MAX_ATTEMPTS": "3",
            "TOOL_BACKOFF_BASE_SECONDS": "0.001",
            "TOOL_MAX_BACKOFF_SECONDS": "0.001",
        }
    )

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout(raw_payload, request=request)

    async def runtime_factory(_settings: Settings) -> PlanningRuntime:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(timeout_handler),
            base_url="https://restapi.amap.com",
        )
        amap_client = AMapClient(client, api_key=secret)
        poi_provider = AMapPOIProvider(amap_client)
        route_provider = AMapRouteProvider(amap_client)
        gateway = build_gateway(settings, poi_provider, route_provider)
        runtime = PlanningRuntime(
            poi_provider=poi_provider,
            route_provider=route_provider,
            gateway=gateway,
            workflow=build_workflow(
                gateway,
                POIDefaultPolicy(UnknownFactPolicy.ASSUME_WITH_WARNING),
            ),
            client=client,
        )
        runtime_box["runtime"] = runtime
        return runtime

    monkeypatch.setattr("travel_agent.api.routes.uuid4", lambda: thread_id)
    caplog.set_level(logging.INFO)
    app = create_app(settings=settings, runtime_factory=runtime_factory)
    request_trip = hangzhou_trip.model_copy(
        update={"must_visit": ["灵隐寺"], "interests": []}
    )

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/v1/plans",
            json={
                "trip": request_trip.model_dump(mode="json"),
                "max_replan_rounds": 2,
            },
        )
        snapshot = test_client.portal.call(
            runtime_box["runtime"].workflow.aget_state,
            {"configurable": {"thread_id": thread_id}},
        )

    assert calls == 3
    assert response.status_code == 503
    assert response.json()["detail"] == {
        "thread_id": thread_id,
        "provider": "amap",
        "operation": "poi.search",
        "category": "timeout",
        "code": "timeout",
        "retryable": True,
        "message": "The provider timed out. Please try again.",
    }
    formatter = logging.Formatter("%(name)s %(levelname)s %(message)s")
    rendered_logs = "\n".join(formatter.format(record) for record in caplog.records)
    public_text = "\n".join(
        [response.text, rendered_logs, repr(snapshot.values)]
    )
    for sensitive in (
        secret,
        "restapi.amap.com",
        "key=",
        "keywords=",
        raw_payload,
        "httpx.ReadTimeout",
    ):
        assert sensitive not in public_text

    messages = [record.getMessage() for record in caplog.records]
    assert any(
        message.startswith("tool.failed")
        and f"thread_id={thread_id}" in message
        and "provider=amap" in message
        and "operation=poi.search" in message
        and "attempt_count=3" in message
        and "category=timeout" in message
        and "code=timeout" in message
        and "retryable=True" in message
        for message in messages
    )
    assert sum(message.startswith("tool.retry_scheduled") for message in messages) == 2
    assert any(message.startswith("planning.failed") for message in messages)
    assert any(message.startswith("api.tool_unavailable") for message in messages)
    assert not any(message.startswith("routing.decision") for message in messages)
    assert snapshot.values["status"] == "search_planned"
    assert snapshot.values["iterations"] == 0
    assert snapshot.values["candidates"] == []
