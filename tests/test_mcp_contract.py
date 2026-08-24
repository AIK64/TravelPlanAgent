from __future__ import annotations

from datetime import date, datetime, timezone
import json

import pytest

from travel_agent.app import create_app
from travel_agent.config import Settings
from travel_agent.domain.models import Coordinate
from travel_agent.domain.weather_models import WeatherLocation
from travel_agent.identity.models import Principal
from travel_agent.mcp_server.server import MCP_PROTOCOL_VERSION, build_mcp_server


@pytest.mark.asyncio
async def test_mcp_contract_exposes_use_case_data_tools_and_resources():
    server = build_mcp_server(
        lambda: (_ for _ in ()).throw(AssertionError("service must be lazy")),
        default_principal=Principal(
            tenant_id="tenant-a",
            user_id="user-a",
            scopes=frozenset({"read:data"}),
        ),
    )

    tools = {tool.name: tool for tool in await server.list_tools()}
    assert {
        "create_travel_plan",
        "resume_travel_run",
        "cancel_travel_run",
        "select_plan_candidate",
        "apply_plan_change",
        "approve_plan_change",
        "get_plan_diff",
        "replay_execution_trace",
        "get_or_update_preferences",
        "search_poi",
        "get_route",
        "get_weather",
    } <= tools.keys()
    assert tools["create_travel_plan"].inputSchema["type"] == "object"
    templates = {
        str(item.uriTemplate) for item in await server.list_resource_templates()
    }
    assert "travel://runs/{run_id}" in templates
    assert "travel://runs/{run_id}/trace" in templates
    assert "travel://plans/{session_id}" in templates
    resources = {str(item.uri) for item in await server.list_resources()}
    assert "travel://users/me/preferences" in resources
    assert MCP_PROTOCOL_VERSION == "2025-11-25"


def test_streamable_http_mcp_initialize(client):
    response = client.post(
        "/mcp/",
        headers={"Accept": "application/json, text/event-stream"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "contract-test", "version": "1.0"},
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["result"]["protocolVersion"] == MCP_PROTOCOL_VERSION
    assert payload["result"]["serverInfo"]["name"] == "travel-agent"


def test_streamable_http_mcp_requires_identity_in_production():
    from fastapi.testclient import TestClient

    application = create_app(Settings.from_env({"DEV_IDENTITY_ENABLED": "false"}))
    with TestClient(application) as client:
        response = client.post(
            "/mcp/",
            headers={"Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "auth-test", "version": "1.0"},
                },
            },
        )
    assert response.status_code == 401
    assert response.json()["code"] == "authentication_required"


class FakeResult:
    def __init__(self, **values) -> None:
        self.__dict__.update(values)

    def model_dump(self, *, mode: str = "python"):
        assert mode in {"python", "json"}
        return {
            key: value.isoformat() if isinstance(value, (date, datetime)) else value
            for key, value in self.__dict__.items()
            if key != "data"
        }

    def model_dump_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), ensure_ascii=False)


class FakePreferenceService:
    async def create_explicit(self, _principal, _create):
        return FakeResult(memory_id="memory-1", category="pace", value="relaxed")

    async def list(self, _principal, *, include_inactive: bool = False):
        return FakeResult(items=[], include_inactive=include_inactive)


class FakeGateway:
    async def search_pois(self, _queries, _context):
        return [FakeResult(status="success", provider="mock", data={"id": "poi-1"})]

    async def get_routes(self, _queries, _context):
        return {"route": FakeResult(status="success", provider="mock", data={})}


class FakeWeatherGateway:
    async def resolve_location(self, destination, _context):
        location = WeatherLocation(
            city_name=destination,
            adcode="330100",
            provider="mock",
            longitude=120.1,
            latitude=30.2,
        )
        return FakeResult(status="success", provider="mock", data=location)

    async def get_forecast(self, _location, **_kwargs):
        return FakeResult(status="success", provider="mock", days=[])


class FakeTravelService:
    def __init__(self) -> None:
        self.runtime = FakeResult(
            gateway=FakeGateway(),
            weather_gateway=FakeWeatherGateway(),
            preference_service=FakePreferenceService(),
        )

    async def create_trip(self, _request, *, principal):
        return FakeResult(trip_id="trip-1", tenant_id=principal.tenant_id)

    async def start_trip_run(self, _trip_id, *, principal):
        return FakeResult(run_id="run-1", tenant_id=principal.tenant_id)

    async def cancel_run(self, run_id, *, principal):
        return FakeResult(run_id=run_id, status="cancelled", tenant_id=principal.tenant_id)

    async def resume_plan_session(self, session_id, _request, *, principal):
        return FakeResult(session_id=session_id, status="completed", tenant_id=principal.tenant_id)

    async def get_plan_diff(self, session_id, from_version_id, to_version_id, *, principal):
        return FakeResult(
            session_id=session_id,
            from_version_id=from_version_id,
            to_version_id=to_version_id,
            tenant_id=principal.tenant_id,
        )

    async def get_trace(self, run_id, *, principal, **_kwargs):
        return (
            FakeResult(
                run_id=run_id,
                event_id="event-1",
                sequence=1,
                tenant_id=principal.tenant_id,
            ),
        )

    async def get_run(self, run_id, *, principal):
        return FakeResult(run_id=run_id, status="completed", tenant_id=principal.tenant_id)

    async def get_plan_session(self, session_id, *, principal):
        return FakeResult(session_id=session_id, status="completed", tenant_id=principal.tenant_id)


@pytest.mark.asyncio
async def test_mcp_tools_and_resources_delegate_to_application_service(hangzhou_trip):
    service = FakeTravelService()
    server = build_mcp_server(
        lambda: service,
        default_principal=Principal(
            tenant_id="tenant-a",
            user_id="user-a",
            scopes=frozenset({"read:data"}),
        ),
    )
    planning_request = {"trip": hangzhou_trip.model_dump(mode="json")}
    calls = [
        ("create_travel_plan", {"request": planning_request}),
        ("cancel_travel_run", {"run_id": "run-1"}),
        (
            "select_plan_candidate",
            {
                "session_id": "session-1",
                "interrupt_id": "interrupt-1",
                "request_id": "550e8400-e29b-41d4-a716-446655440000",
                "candidate_id": "candidate-1",
            },
        ),
        (
            "apply_plan_change",
            {
                "session_id": "session-1",
                "interrupt_id": "interrupt-1",
                "request_id": "550e8400-e29b-41d4-a716-446655440001",
                "text": "第二天节奏慢一点",
            },
        ),
        (
            "approve_plan_change",
            {
                "session_id": "session-1",
                "interrupt_id": "interrupt-2",
                "request_id": "550e8400-e29b-41d4-a716-446655440002",
                "preview_id": "preview-1",
                    "approval_token": "approval-token-123456",
            },
        ),
        (
            "get_plan_diff",
            {
                "session_id": "session-1",
                "from_version_id": "v1",
                "to_version_id": "v2",
            },
        ),
        ("replay_execution_trace", {"run_id": "run-1", "limit": 10}),
        (
            "get_or_update_preferences",
            {"create": {"category": "pace", "value": "relaxed"}},
        ),
        (
            "search_poi",
            {"query": {"city": "杭州", "keyword": "寺庙", "limit": 1}},
        ),
        (
            "get_route",
            {
                "query": {
                    "origin": Coordinate(longitude=120.1, latitude=30.2).model_dump(),
                    "destination": Coordinate(longitude=120.2, latitude=30.3).model_dump(),
                }
            },
        ),
        (
            "get_weather",
            {
                "destination": "杭州",
                "start_date": "2026-10-02",
                "end_date": "2026-10-03",
            },
        ),
    ]
    for name, arguments in calls:
        assert await server.call_tool(name, arguments)

    assert await server.read_resource("travel://runs/run-1")
    assert await server.read_resource("travel://runs/run-1/trace")
    assert await server.read_resource("travel://plans/session-1")
    assert await server.read_resource("travel://users/me/preferences")


@pytest.mark.asyncio
async def test_mcp_low_level_data_tools_require_scope():
    server = build_mcp_server(
        lambda: FakeTravelService(),
        default_principal=Principal(tenant_id="tenant-a", user_id="user-a"),
    )
    with pytest.raises(Exception, match="missing required scope"):
        await server.call_tool(
            "search_poi",
            {"query": {"city": "杭州", "keyword": "寺庙", "limit": 1}},
        )
