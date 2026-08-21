from __future__ import annotations

import logging
from datetime import datetime, time, timezone
from decimal import Decimal

import pytest

from travel_agent.config import Settings
from travel_agent.domain.models import Coordinate, PlanningRequest
from travel_agent.domain.tool_models import (
    POIFacts,
    POISearchQuery,
    RouteQuery,
    RouteResult,
    ToolStatus,
    UnknownFactPolicy,
    route_key,
)
from travel_agent.graph.workflow import build_workflow, run_planning
from travel_agent.planning.defaults import POIDefaultPolicy
from travel_agent.runtime import PlanningRuntime
from travel_agent.tools.errors import ToolProviderError, ToolUnavailableError
from travel_agent.tools.providers.mock import MockPOIProvider, MockRouteProvider


def _event_names(records: list[logging.LogRecord], thread_id: str) -> list[str]:
    marker = f"thread_id={thread_id}"
    return [
        record.getMessage().split(maxsplit=1)[0]
        for record in records
        if marker in record.getMessage()
    ]


def _assert_in_order(actual: list[str], expected: list[str]) -> None:
    position = 0
    for event in actual:
        if position < len(expected) and event == expected[position]:
            position += 1
    assert position == len(expected), actual


@pytest.mark.asyncio
async def test_agent_trajectory_calls_tools_updates_state_and_selects(
    hangzhou_trip,
    workflow_harness,
    caplog,
):
    caplog.set_level(logging.INFO)
    thread_id = "trajectory-success"

    response = await run_planning(
        workflow_harness.workflow,
        PlanningRequest(trip=hangzhou_trip, max_replan_rounds=2),
        thread_id=thread_id,
    )

    assert response.status == "completed"
    assert workflow_harness.poi_provider.calls
    assert workflow_harness.route_provider.calls
    events = _event_names(caplog.records, thread_id)
    _assert_in_order(
        events,
        [
            "search_plan.created",
            "tool.started",
            "poi_context.loaded",
            "candidate_drafts.prepared",
            "tool.started",
            "routes.loaded",
            "candidate.generated",
            "candidate.validated",
            "routing.decision",
            "plan.selected",
        ],
    )

    snapshot = await workflow_harness.workflow.aget_state(
        {"configurable": {"thread_id": thread_id}}
    )
    state = snapshot.values
    assert state["thread_id"] == thread_id
    assert state["search_queries"]
    assert state["poi_facts"]
    assert state["planning_pois"]
    assert state["candidate_drafts"]
    assert state["route_queries"]
    assert state["route_results"]
    assert state["candidates"]
    assert state["selected_plan"] == response.selected_plan
    assert {summary.operation for summary in state["tool_summaries"]} == {
        "poi.search",
        "route.get_driving",
    }
    assert all(
        summary.status is ToolStatus.SUCCESS
        for summary in state["tool_summaries"]
    )
    assert all(summary.attempt_count == 1 for summary in state["tool_summaries"])
    assert not {
        "gateway",
        "provider",
        "raw_payload",
        "http_client",
        "api_key",
        "exception",
    } & set(state)

    history = [
        item
        async for item in workflow_harness.workflow.aget_state_history(
            {"configurable": {"thread_id": thread_id}}
        )
    ]
    state_before_node = {
        item.next[0]: item.values
        for item in history
        if len(item.next) == 1
    }
    assert state_before_node["load_pois"]["search_queries"]
    assert state_before_node["load_pois"]["poi_facts"] == []
    assert state_before_node["resolve_poi_facts"]["poi_facts"]
    assert state_before_node["resolve_poi_facts"]["planning_pois"] == []
    assert state_before_node["prepare_candidate_drafts"]["planning_pois"]
    assert state_before_node["prepare_candidate_drafts"]["candidate_drafts"] == []
    assert state_before_node["materialize_candidates"]["route_queries"]
    assert state_before_node["materialize_candidates"]["route_results"]
    assert state_before_node["validate_candidates"]["candidates"]
    assert not any(
        record.getMessage().startswith("Deserializing unregistered type")
        for record in caplog.records
    )

    other_thread = "trajectory-isolated"
    await run_planning(
        workflow_harness.workflow,
        PlanningRequest(
            trip=hangzhou_trip.model_copy(update={"must_visit": []}),
            max_replan_rounds=0,
        ),
        thread_id=other_thread,
    )
    first_snapshot = await workflow_harness.workflow.aget_state(
        {"configurable": {"thread_id": thread_id}}
    )
    other_snapshot = await workflow_harness.workflow.aget_state(
        {"configurable": {"thread_id": other_thread}}
    )
    assert first_snapshot.values["trip"].must_visit == ["灵隐寺"]
    assert other_snapshot.values["trip"].must_visit == []


class AuthenticationFailurePOIProvider(MockPOIProvider):
    async def search_pois(self, query: POISearchQuery) -> list[POIFacts]:
        raise ToolProviderError.authentication("poi.search")


class AlwaysTimeoutAMapPOIProvider:
    name = "amap"

    def __init__(self) -> None:
        self.calls = 0

    async def search_pois(self, query: POISearchQuery) -> list[POIFacts]:
        self.calls += 1
        raise ToolProviderError.timeout("poi.search")


class UnusedAMapRouteProvider:
    name = "amap"

    async def get_driving_route(self, query: RouteQuery) -> RouteResult:
        raise AssertionError("POI failure must stop before route lookup")


class ExplodingUnselectedMockProvider(MockPOIProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def search_pois(self, query: POISearchQuery) -> list[POIFacts]:
        self.calls += 1
        raise AssertionError("unselected Mock provider must never be called")


class ExplodingUnselectedMockRouteProvider(MockRouteProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def get_driving_route(self, query: RouteQuery) -> RouteResult:
        self.calls += 1
        raise AssertionError("unselected Mock route provider must never be called")


class RuntimeClientProbe:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class TimeoutRouteProvider(MockRouteProvider):
    async def get_driving_route(self, query: RouteQuery) -> RouteResult:
        raise ToolProviderError.timeout("route.get_driving")


class FailRepeatedRouteProvider(MockRouteProvider):
    """首轮每条路线成功；后续 round 重复路线时模拟供应商超时。"""

    def __init__(self) -> None:
        self.seen_keys: set[str] = set()

    async def get_driving_route(self, query: RouteQuery) -> RouteResult:
        key = route_key(query)
        if key in self.seen_keys:
            raise ToolProviderError.timeout("route.get_driving")
        self.seen_keys.add(key)
        return await super().get_driving_route(query)


class PriorityPOIProvider:
    name = "priority-fixture"

    def __init__(self) -> None:
        self.calls: list[POISearchQuery] = []

    @staticmethod
    def _facts(poi_id: str, name: str, index: int) -> POIFacts:
        return POIFacts(
            id=poi_id,
            name=name,
            city="杭州",
            coordinate=Coordinate(
                longitude=120.10 + index * 0.001,
                latitude=30.20 + index * 0.001,
            ),
            categories=["fixture"],
            provider="priority-fixture",
            fetched_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
        )

    async def search_pois(self, query: POISearchQuery) -> list[POIFacts]:
        self.calls.append(query)
        if query.keyword == "priority":
            return [
                self._facts(
                    f"high-{index}",
                    "priority" if index == 0 else f"high {index}",
                    index,
                )
                for index in range(8)
            ]
        return [
            self._facts("high-0", "priority", 0),
            *[
                self._facts(f"low-{index}", f"low {index}", index + 8)
                for index in range(8)
            ],
        ]


class ConstantRouteProvider:
    name = "constant-route"

    def __init__(self) -> None:
        self.calls: list[RouteQuery] = []

    async def get_driving_route(self, query: RouteQuery) -> RouteResult:
        self.calls.append(query)
        return RouteResult(
            distance_meters=1_000,
            duration_minutes=10,
            provider=self.name,
            data_confidence=1.0,
            fetched_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
        )


@pytest.mark.asyncio
async def test_poi_tool_failure_raises_without_validation_or_replan(
    hangzhou_trip,
    gateway_factory,
    caplog,
):
    caplog.set_level(logging.INFO)
    thread_id = "trajectory-poi-failure"
    workflow = build_workflow(
        gateway_factory(poi_provider=AuthenticationFailurePOIProvider()),
        POIDefaultPolicy(UnknownFactPolicy.ASSUME_WITH_WARNING),
    )

    with pytest.raises(ToolUnavailableError) as error:
        await run_planning(
            workflow,
            PlanningRequest(trip=hangzhou_trip, max_replan_rounds=2),
            thread_id=thread_id,
        )

    assert error.value.thread_id == thread_id
    assert error.value.result.status is ToolStatus.FAILED
    assert error.value.result.attempt_count == 1
    assert error.value.safe_detail()["operation"] == "poi.search"
    events = _event_names(caplog.records, thread_id)
    assert "tool.failed" in events
    assert "planning.failed" in events
    assert not {
        "candidate.validated",
        "routing.decision",
        "replan.round_started",
        "planning.infeasible",
    } & set(events)
    snapshot = await workflow.aget_state(
        {"configurable": {"thread_id": thread_id}}
    )
    assert snapshot.values["status"] == "search_planned"
    assert snapshot.values["iterations"] == 0
    assert snapshot.values["candidates"] == []


@pytest.mark.asyncio
async def test_amap_failure_retries_selected_provider_without_mock_fallback(
    hangzhou_trip,
    monkeypatch,
    caplog,
):
    """AMap 失败只能耗尽选中 Provider 的公开重试预算，不能静默回退 Mock。"""
    selected_amap = AlwaysTimeoutAMapPOIProvider()
    selected_route = UnusedAMapRouteProvider()
    unselected_mock_poi = ExplodingUnselectedMockProvider()
    unselected_mock_route = ExplodingUnselectedMockRouteProvider()
    mock_constructor_calls = {"poi": 0, "route": 0}
    client = RuntimeClientProbe()

    def construct_mock_poi():
        mock_constructor_calls["poi"] += 1
        return unselected_mock_poi

    def construct_mock_route():
        mock_constructor_calls["route"] += 1
        return unselected_mock_route

    monkeypatch.setattr(
        "travel_agent.runtime.httpx.AsyncClient", lambda **_kwargs: client
    )
    monkeypatch.setattr(
        "travel_agent.runtime.AMapPOIProvider", lambda _client: selected_amap
    )
    monkeypatch.setattr(
        "travel_agent.runtime.AMapRouteProvider", lambda _client: selected_route
    )
    monkeypatch.setattr("travel_agent.runtime.MockPOIProvider", construct_mock_poi)
    monkeypatch.setattr("travel_agent.runtime.MockRouteProvider", construct_mock_route)
    settings = Settings.from_env(
        {
            "TRAVEL_PROVIDER": "amap",
            "AMAP_API_KEY": "assembly-only-test-key",
            "TOOL_MAX_ATTEMPTS": "3",
            "TOOL_BACKOFF_BASE_SECONDS": "0.001",
            "TOOL_MAX_BACKOFF_SECONDS": "0.001",
        }
    )
    runtime = await PlanningRuntime.create(settings)
    request = PlanningRequest(
        trip=hangzhou_trip.model_copy(
            update={"must_visit": ["灵隐寺"], "interests": []}
        ),
        max_replan_rounds=2,
    )
    thread_id = "amap-no-fallback"
    caplog.set_level(logging.INFO)

    try:
        with pytest.raises(ToolUnavailableError) as raised:
            await runtime.plan(request, thread_id=thread_id)
    finally:
        await runtime.close()

    assert raised.value.result.attempt_count == 3
    assert selected_amap.calls == 3
    assert mock_constructor_calls == {"poi": 0, "route": 0}
    assert unselected_mock_poi.calls == 0
    assert unselected_mock_route.calls == 0
    assert runtime.poi_provider is selected_amap
    assert runtime.route_provider is selected_route
    assert all(
        unselected_mock_poi is not owned and unselected_mock_route is not owned
        for owned in (
            runtime.poi_provider,
            runtime.route_provider,
            runtime.gateway,
            runtime.workflow,
            runtime.client,
        )
    )
    assert not hasattr(runtime, "fallback_provider")
    assert client.closed
    events = _event_names(caplog.records, thread_id)
    assert events.count("tool.retry_scheduled") == 2
    assert "tool.failed" in events
    assert "planning.failed" in events
    assert not {
        "candidate.validated",
        "routing.decision",
        "replan.round_started",
        "planning.infeasible",
    } & set(events)
    snapshot = await runtime.workflow.aget_state(
        {"configurable": {"thread_id": thread_id}}
    )
    assert snapshot.values["status"] == "search_planned"
    assert snapshot.values["iterations"] == 0
    assert snapshot.values["candidates"] == []


@pytest.mark.asyncio
async def test_route_tool_failure_raises_after_retry_without_business_replan(
    hangzhou_trip,
    gateway_factory,
    caplog,
):
    caplog.set_level(logging.INFO)
    thread_id = "trajectory-route-failure"
    workflow = build_workflow(
        gateway_factory(
            route_provider=TimeoutRouteProvider(),
            max_attempts=2,
        ),
        POIDefaultPolicy(UnknownFactPolicy.ASSUME_WITH_WARNING),
    )

    with pytest.raises(ToolUnavailableError) as error:
        await run_planning(
            workflow,
            PlanningRequest(trip=hangzhou_trip, max_replan_rounds=2),
            thread_id=thread_id,
        )

    assert error.value.thread_id == thread_id
    assert error.value.result.status is ToolStatus.FAILED
    assert error.value.result.attempt_count == 2
    assert error.value.safe_detail()["operation"] == "route.get_driving"
    events = _event_names(caplog.records, thread_id)
    assert "tool.retry_scheduled" in events
    assert "tool.failed" in events
    assert "planning.failed" in events
    assert not {
        "candidate.validated",
        "routing.decision",
        "replan.round_started",
        "planning.infeasible",
    } & set(events)
    snapshot = await workflow.aget_state(
        {"configurable": {"thread_id": thread_id}}
    )
    assert snapshot.values["status"] == "candidate_drafts_prepared"
    assert snapshot.values["iterations"] == 0
    assert snapshot.values["candidates"] == []


@pytest.mark.asyncio
async def test_replan_route_failure_does_not_commit_iteration_or_stale_round_state(
    hangzhou_trip,
    gateway_factory,
    caplog,
):
    caplog.set_level(logging.INFO)
    thread_id = "trajectory-replan-route-failure"
    workflow = build_workflow(
        gateway_factory(
            route_provider=FailRepeatedRouteProvider(),
            max_attempts=2,
            route_cache_ttl_seconds=0,
        ),
        POIDefaultPolicy(UnknownFactPolicy.ASSUME_WITH_WARNING),
    )
    low_budget_trip = hangzhou_trip.model_copy(
        update={"total_budget": Decimal("10")}
    )

    with pytest.raises(ToolUnavailableError) as error:
        await run_planning(
            workflow,
            PlanningRequest(trip=low_budget_trip, max_replan_rounds=2),
            thread_id=thread_id,
        )

    assert error.value.result.status is ToolStatus.FAILED
    assert error.value.result.attempt_count == 2
    assert error.value.safe_detail()["operation"] == "route.get_driving"
    snapshot = await workflow.aget_state(
        {"configurable": {"thread_id": thread_id}}
    )
    state = snapshot.values
    assert state["iterations"] == 0
    assert state["pending_replan_round"] == 1
    assert {draft.id.rsplit("-", 1)[-1] for draft in state["candidate_drafts"]} == {
        "r1"
    }
    assert state["route_queries"] == []
    assert state["route_results"] == {}
    assert state["candidates"] == []
    assert state["selected_plan"] is None
    assert any(
        summary.operation == "route.get_driving"
        and summary.status is ToolStatus.SUCCESS
        for summary in state["tool_summaries"]
    )
    history = [
        item
        async for item in workflow.aget_state_history(
            {"configurable": {"thread_id": thread_id}}
        )
    ]
    assert any(item.values["route_results"] for item in history)
    assert any(
        candidate.validation is not None
        for item in history
        for candidate in item.values["candidates"]
    )

    messages = [
        record.getMessage()
        for record in caplog.records
        if f"thread_id={thread_id}" in record.getMessage()
    ]
    decisions = [
        message.split("next=", 1)[1].split(maxsplit=1)[0]
        for message in messages
        if message.startswith("routing.decision")
    ]
    events = _event_names(caplog.records, thread_id)
    assert decisions == ["replan"]
    assert events.count("candidate.validated") == 3
    assert "replan.round_started" in events
    assert "replan.completed" not in events
    assert "planning.infeasible" not in events
    assert "tool.retry_scheduled" in events
    assert "planning.failed" in events


@pytest.mark.asyncio
async def test_validation_feedback_drives_one_bounded_replan(
    hangzhou_trip,
    workflow_harness,
    caplog,
):
    caplog.set_level(logging.INFO)
    thread_id = "trajectory-replan"
    low_budget_trip = hangzhou_trip.model_copy(
        update={"total_budget": Decimal("10")}
    )

    response = await run_planning(
        workflow_harness.workflow,
        PlanningRequest(trip=low_budget_trip, max_replan_rounds=1),
        thread_id=thread_id,
    )

    assert response.status == "infeasible"
    assert response.selected_plan is None
    assert response.iterations == 1
    messages = [
        record.getMessage()
        for record in caplog.records
        if f"thread_id={thread_id}" in record.getMessage()
    ]
    decisions = [
        message.split("next=", 1)[1].split(maxsplit=1)[0]
        for message in messages
        if message.startswith("routing.decision")
    ]
    assert decisions == ["replan", "mark_infeasible"]
    events = _event_names(caplog.records, thread_id)
    _assert_in_order(
        events,
        [
            "routing.decision",
            "replan.round_started",
            "candidate_drafts.prepared",
            "tool.started",
            "routes.loaded",
            "candidate.generated",
            "candidate.validated",
            "replan.completed",
            "routing.decision",
            "planning.infeasible",
        ],
    )
    snapshot = await workflow_harness.workflow.aget_state(
        {"configurable": {"thread_id": thread_id}}
    )
    route_summaries = [
        summary
        for summary in snapshot.values["tool_summaries"]
        if summary.operation == "route.get_driving"
    ]
    assert any(not summary.cache_hit for summary in route_summaries)
    assert any(summary.cache_hit for summary in route_summaries)
    assert any(
        message.startswith("replan.completed")
        and "round=1" in message
        and "status=validated" in message
        for message in messages
    )


@pytest.mark.asyncio
async def test_daily_window_violation_replans_then_stops_without_selecting(
    hangzhou_trip,
    workflow_harness,
    caplog,
):
    """防止越界必去地点绕过 Validator 并直接进入 select_best。"""
    caplog.set_level(logging.INFO)
    thread_id = "trajectory-daily-window"
    constrained = hangzhou_trip.model_copy(update={"daily_end": time(12, 0)})

    response = await run_planning(
        workflow_harness.workflow,
        PlanningRequest(trip=constrained, max_replan_rounds=1),
        thread_id=thread_id,
    )

    decisions = [
        record.getMessage().split("next=", 1)[1].split(maxsplit=1)[0]
        for record in caplog.records
        if record.getMessage().startswith("routing.decision")
        and f"thread_id={thread_id}" in record.getMessage()
    ]
    events = _event_names(caplog.records, thread_id)
    assert response.status == "infeasible"
    assert response.selected_plan is None
    assert response.iterations == 1
    assert decisions == ["replan", "mark_infeasible"]
    assert "plan.selected" not in events
    assert all(
        "missing_must_visit"
        in {violation.type for violation in candidate.validation.violations}
        for candidate in response.candidates
        if candidate.validation is not None
    )


@pytest.mark.asyncio
async def test_poi_context_merges_by_priority_deduplicates_and_caps_at_twelve(
    hangzhou_trip,
    gateway_factory,
):
    poi_provider = PriorityPOIProvider()
    workflow = build_workflow(
        gateway_factory(
            poi_provider=poi_provider,
            route_provider=ConstantRouteProvider(),
        ),
        POIDefaultPolicy(UnknownFactPolicy.ASSUME_WITH_WARNING),
    )
    trip = hangzhou_trip.model_copy(
        update={"must_visit": ["priority"], "interests": ["low"]}
    )
    thread_id = "trajectory-poi-merge"

    await run_planning(
        workflow,
        PlanningRequest(trip=trip, max_replan_rounds=0),
        thread_id=thread_id,
    )

    snapshot = await workflow.aget_state(
        {"configurable": {"thread_id": thread_id}}
    )
    assert [facts.id for facts in snapshot.values["poi_facts"]] == [
        *[f"high-{index}" for index in range(8)],
        *[f"low-{index}" for index in range(4)],
    ]
    assert len(snapshot.values["planning_pois"]) == 12
    assert [query.priority for query in poi_provider.calls] == [100, 50]


@pytest.mark.asyncio
async def test_runtime_planning_policy_reaches_checkpoint_and_provider_calls(
    hangzhou_trip,
    monkeypatch,
):
    """防止非默认公开设置在 Runtime 装配后被 Graph 的硬编码静默覆盖。"""
    poi_provider = MockPOIProvider()
    route_provider = MockRouteProvider()
    poi_calls: list[POISearchQuery] = []
    route_calls: list[RouteQuery] = []
    original_search = poi_provider.search_pois
    original_route = route_provider.get_driving_route

    async def record_search(query: POISearchQuery) -> list[POIFacts]:
        poi_calls.append(query)
        return await original_search(query)

    async def record_route(query: RouteQuery) -> RouteResult:
        route_calls.append(query)
        return await original_route(query)

    monkeypatch.setattr(poi_provider, "search_pois", record_search)
    monkeypatch.setattr(route_provider, "get_driving_route", record_route)
    monkeypatch.setattr(
        "travel_agent.runtime.MockPOIProvider",
        lambda: poi_provider,
    )
    monkeypatch.setattr(
        "travel_agent.runtime.MockRouteProvider",
        lambda: route_provider,
    )
    settings = Settings.from_env(
        {
            "POI_QUERY_LIMIT": "1",
            "POI_CANDIDATE_LIMIT": "1",
            "POI_MAX_QUERIES": "2",
            "AMAP_DRIVING_STRATEGY": "7",
        }
    )
    runtime = await PlanningRuntime.create(settings)
    thread_id = "runtime-planning-policy"
    trip = hangzhou_trip.model_copy(
        update={
            "must_visit": ["灵隐寺", "西湖"],
            "interests": ["自然", "美食", "人文"],
        }
    )

    try:
        await runtime.plan(
            PlanningRequest(trip=trip, max_replan_rounds=0),
            thread_id=thread_id,
        )
        snapshot = await runtime.workflow.aget_state(
            {"configurable": {"thread_id": thread_id}}
        )
    finally:
        await runtime.close()

    state = snapshot.values
    assert [query.keyword for query in state["search_queries"]] == [
        "灵隐寺",
        "西湖",
    ]
    assert all(query.limit == 1 for query in state["search_queries"])
    assert len(state["poi_facts"]) == 1
    assert len(poi_calls) == 2
    assert [query.keyword for query in poi_calls] == ["灵隐寺", "西湖"]
    assert route_calls
    assert all(query.strategy == 7 for query in state["route_queries"])
    assert all(query.strategy == 7 for query in route_calls)
    assert not hasattr(runtime, "settings")
    assert not hasattr(runtime, "planning_policy")


@pytest.mark.asyncio
async def test_maximum_business_replan_budget_stops_before_recursion_guard(
    hangzhou_trip,
    workflow_harness,
    caplog,
):
    caplog.set_level(logging.INFO)
    thread_id = "trajectory-max-replan"
    low_budget_trip = hangzhou_trip.model_copy(
        update={"total_budget": Decimal("10")}
    )

    response = await run_planning(
        workflow_harness.workflow,
        PlanningRequest(trip=low_budget_trip, max_replan_rounds=5),
        thread_id=thread_id,
    )

    decisions = [
        record.getMessage().split("next=", 1)[1].split(maxsplit=1)[0]
        for record in caplog.records
        if record.getMessage().startswith("routing.decision")
        and f"thread_id={thread_id}" in record.getMessage()
    ]
    assert response.status == "infeasible"
    assert response.iterations == 5
    assert decisions == ["replan"] * 5 + ["mark_infeasible"]
    events = _event_names(caplog.records, thread_id)
    assert events.count("replan.round_started") == 5
    assert events.count("replan.completed") == 5
    snapshot = await workflow_harness.workflow.aget_state(
        {"configurable": {"thread_id": thread_id}}
    )
    assert snapshot.values["pending_replan_round"] is None
