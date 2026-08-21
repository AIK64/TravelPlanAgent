from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import logging

import pytest
from fastapi.testclient import TestClient

from travel_agent import __version__
from travel_agent.app import create_app
from travel_agent.config import Settings
from travel_agent.domain.models import PlanningResponse
from travel_agent.domain.tool_models import ProviderMode
from travel_agent.runtime import PlanningRuntime
from travel_agent.tools.providers.amap import AMapPOIProvider, AMapRouteProvider
from travel_agent.tools.providers.mock import MockPOIProvider, MockRouteProvider


RUNTIME_OWNED_FIELDS = {
    "poi_provider",
    "route_provider",
    "gateway",
    "workflow",
    "client",
}


@dataclass
class RuntimeProbe:
    plan_calls: list[tuple[object, str]]
    closed: bool = False

    async def plan(self, request, thread_id: str) -> PlanningResponse:
        self.plan_calls.append((request, thread_id))
        return PlanningResponse(
            status="infeasible",
            selected_plan=None,
            candidates=[],
            iterations=0,
        )

    async def close(self) -> None:
        self.closed = True


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_openapi_version_matches_package_version(client):
    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert __version__ == "0.2.0"
    assert response.json()["info"]["version"] == __version__


def test_create_plan(client, hangzhou_trip):
    response = client.post(
        "/api/v1/plans",
        json={
            "trip": hangzhou_trip.model_dump(mode="json"),
            "max_replan_rounds": 2,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["selected_plan"]["validation"]["valid"] is True


def test_json_response_declares_utf8_for_legacy_windows_clients(
    client, hangzhou_trip
):
    response = client.post(
        "/api/v1/plans",
        json={
            "trip": hangzhou_trip.model_dump(mode="json"),
            "max_replan_rounds": 2,
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].lower() == "application/json; charset=utf-8"


@pytest.mark.asyncio
async def test_mock_runtime_contains_only_mock_providers():
    runtime = await PlanningRuntime.create(Settings.from_env({}))
    try:
        assert set(runtime.__slots__) == RUNTIME_OWNED_FIELDS
        assert not hasattr(runtime, "settings")
        assert isinstance(runtime.poi_provider, MockPOIProvider)
        assert isinstance(runtime.route_provider, MockRouteProvider)
        assert runtime.client is None
        assert not hasattr(runtime, "fallback_provider")
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_amap_runtime_contains_only_amap_providers_and_closes_client():
    settings = Settings.from_env(
        {"TRAVEL_PROVIDER": "amap", "AMAP_API_KEY": "test-key"}
    )
    runtime = await PlanningRuntime.create(settings)
    client = runtime.client

    assert settings.provider is ProviderMode.AMAP
    assert set(runtime.__slots__) == RUNTIME_OWNED_FIELDS
    assert not hasattr(runtime, "settings")
    assert isinstance(runtime.poi_provider, AMapPOIProvider)
    assert isinstance(runtime.route_provider, AMapRouteProvider)
    assert client is not None
    assert not client.is_closed
    assert not hasattr(runtime, "fallback_provider")

    await runtime.close()

    assert client.is_closed


@pytest.mark.asyncio
async def test_amap_runtime_closes_client_when_assembly_fails(monkeypatch):
    class ClientProbe:
        def __init__(self):
            self.closed = False

        async def aclose(self):
            self.closed = True

    client = ClientProbe()

    def fail_workflow(*_args):
        raise RuntimeError("workflow assembly failed")

    monkeypatch.setattr(
        "travel_agent.runtime.httpx.AsyncClient", lambda **_kwargs: client
    )
    monkeypatch.setattr("travel_agent.runtime.build_workflow", fail_workflow)
    settings = Settings.from_env(
        {"TRAVEL_PROVIDER": "amap", "AMAP_API_KEY": "test-key"}
    )

    with pytest.raises(RuntimeError, match="workflow assembly failed"):
        await PlanningRuntime.create(settings)

    assert client.closed


def test_amap_app_creation_without_key_fails(monkeypatch):
    monkeypatch.setenv("TRAVEL_PROVIDER", "amap")
    monkeypatch.delenv("AMAP_API_KEY", raising=False)

    with pytest.raises(ValueError, match="AMAP_API_KEY"):
        create_app()


def test_lifespan_stores_one_runtime_and_closes_it():
    settings = Settings.from_env({})
    runtime = RuntimeProbe(plan_calls=[])
    factory_settings = []

    async def runtime_factory(received_settings):
        factory_settings.append(received_settings)
        return runtime

    application = create_app(settings=settings, runtime_factory=runtime_factory)

    with TestClient(application):
        assert application.state.planning_runtime is runtime
        assert factory_settings == [settings]
        assert not runtime.closed

    assert runtime.closed


def test_async_endpoint_awaits_runtime_from_lifespan(monkeypatch, hangzhou_trip):
    runtime = RuntimeProbe(plan_calls=[])

    async def runtime_factory(_settings):
        return runtime

    monkeypatch.setattr("travel_agent.api.routes.uuid4", lambda: "api-runtime")
    application = create_app(
        settings=Settings.from_env({}), runtime_factory=runtime_factory
    )

    with TestClient(application) as test_client:
        response = test_client.post(
            "/api/v1/plans",
            json={"trip": hangzhou_trip.model_dump(mode="json")},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "infeasible"
    assert len(runtime.plan_calls) == 1
    assert runtime.plan_calls[0][1] == "api-runtime"
    assert runtime.closed


def test_business_infeasible_still_returns_200(client, hangzhou_trip):
    low_budget_trip = hangzhou_trip.model_copy(
        update={"total_budget": Decimal("10")}
    )

    response = client.post(
        "/api/v1/plans",
        json={
            "trip": low_budget_trip.model_dump(mode="json"),
            "max_replan_rounds": 1,
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "infeasible"


def test_invalid_request_keeps_fastapi_4xx_semantics(client):
    response = client.post("/api/v1/plans", json={})

    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "missing"


def test_tool_failure_returns_503_with_only_safe_detail(
    tool_failure_client, hangzhou_trip
):
    response = tool_failure_client.post(
        "/api/v1/plans",
        json={"trip": hangzhou_trip.model_dump(mode="json")},
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "tool_unavailable",
            "provider": "amap",
            "operation": "search_pois",
            "category": "timeout",
            "retryable": True,
            "thread_id": "api-tool-failure",
            "message": "地图服务暂时不可用，请稍后重试",
        }
    }


def test_tool_failure_logs_error_with_traceback(
    tool_failure_client, hangzhou_trip, caplog
):
    caplog.set_level(logging.ERROR, logger="travel_agent.api.errors")

    response = tool_failure_client.post(
        "/api/v1/plans",
        json={"trip": hangzhou_trip.model_dump(mode="json")},
    )

    assert response.status_code == 503
    records = [
        record
        for record in caplog.records
        if record.name == "travel_agent.api.errors"
    ]
    assert len(records) == 1
    assert records[0].levelno == logging.ERROR
    assert records[0].exc_info is not None
