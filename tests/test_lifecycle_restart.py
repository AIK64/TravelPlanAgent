from uuid import uuid4

import pytest

from travel_agent.config import Settings
from travel_agent.domain.lifecycle_models import LifecycleResumeRequest
from travel_agent.domain.models import PlanningRequest
from travel_agent.domain.weather_models import WeatherRefreshRequest
from travel_agent.domain.tool_models import ToolErrorCategory
from travel_agent.domain.weather_models import WeatherLocation
from travel_agent.runtime import PlanningRuntime
from travel_agent.tools.errors import ToolProviderError
from travel_agent.weather.errors import WeatherUnavailableError


@pytest.mark.asyncio
async def test_sqlite_runtime_restores_candidate_selection_interrupt(
    tmp_path, hangzhou_trip
):
    pytest.importorskip("aiosqlite")
    settings = Settings.from_env(
        {
            "CHECKPOINT_BACKEND": "sqlite",
            "CHECKPOINT_SQLITE_PATH": str(tmp_path / "checkpoints.sqlite3"),
            "PLAN_SQLITE_PATH": str(tmp_path / "plans.sqlite3"),
        }
    )
    first = await PlanningRuntime.create(settings)
    created = await first.create_plan_session(
        PlanningRequest(trip=hangzhou_trip), session_id="restart-session"
    )
    interrupt_id = created.interrupt.id
    await first.close()

    second = await PlanningRuntime.create(settings)
    try:
        restored = await second.get_plan_session(session_id="restart-session")
        assert restored.status.value == "awaiting_candidate_selection"
        assert restored.interrupt is not None
        assert restored.interrupt.id == interrupt_id

        resumed = await second.resume_plan_session(
            LifecycleResumeRequest(
                interrupt_id=restored.interrupt.id,
                request_id=uuid4(),
                expected_session_revision=0,
                action={"kind": "accept_recommendation"},
            ),
            session_id="restart-session",
        )
        assert resumed.active_version is not None
        assert resumed.active_version.version_id == "V1"
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_sqlite_runtime_restores_weather_event_preview_and_approval(
    tmp_path, hangzhou_trip
):
    pytest.importorskip("aiosqlite")
    settings = Settings.from_env(
        {
            "CHECKPOINT_BACKEND": "sqlite",
            "CHECKPOINT_SQLITE_PATH": str(tmp_path / "weather-checkpoints.sqlite3"),
            "PLAN_SQLITE_PATH": str(tmp_path / "weather-plans.sqlite3"),
        }
    )
    first = await PlanningRuntime.create(settings)
    created = await first.create_plan_session(
        PlanningRequest(trip=hangzhou_trip), session_id="weather-restart-session"
    )
    active = await first.resume_plan_session(
        LifecycleResumeRequest(
            interrupt_id=created.interrupt.id,
            request_id=uuid4(),
            expected_session_revision=0,
            action={"kind": "accept_recommendation"},
        ),
        session_id="weather-restart-session",
    )
    preview = await first.refresh_plan_weather(
        WeatherRefreshRequest(
            request_id=uuid4(),
            expected_active_version_id="V1",
            expected_session_revision=active.session_revision,
        ),
        session_id="weather-restart-session",
    )
    event_id = preview.pending_preview.change_trigger.event_id
    preview_id = preview.pending_preview.preview_id
    await first.close()

    second = await PlanningRuntime.create(settings)
    try:
        restored = await second.get_plan_session(
            session_id="weather-restart-session"
        )
        assert restored.status.value == "awaiting_change_approval"
        assert restored.pending_preview.preview_id == preview_id
        assert restored.weather.monitor.latest_event_id == event_id
        assert restored.interrupt.payload["change_source"] == "weather"

        event = await second.get_plan_weather_event(
            session_id="weather-restart-session", event_id=event_id
        )
        assert event.receipt.status.value == "preview_created"
        approved = await second.resume_plan_session(
            LifecycleResumeRequest(
                interrupt_id=restored.interrupt.id,
                request_id=uuid4(),
                expected_active_version_id="V1",
                expected_session_revision=restored.session_revision,
                action={
                    "kind": "approve_preview",
                    "preview_id": preview_id,
                    "approval_token": restored.interrupt.payload["approval_token"],
                },
            ),
            session_id="weather-restart-session",
        )
        assert approved.active_version.version_id == "V2"
        assert approved.active_version.change_trigger.event_id == event_id
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_weather_provider_failure_keeps_active_plan_and_is_not_infeasible(
    monkeypatch, hangzhou_trip
):
    class FailingWeatherProvider:
        name = "failed-weather"

        async def resolve_location(self, _destination):
            return WeatherLocation(
                city_name="杭州市",
                adcode="330100",
                provider=self.name,
            )

        async def get_forecast(self, _location, *, start_date, end_date):
            raise ToolProviderError(
                category=ToolErrorCategory.UPSTREAM_UNAVAILABLE,
                code="weather_upstream_unavailable",
                operation="weather.get_forecast",
                retryable=False,
                safe_message="天气服务暂时不可用",
            )

    monkeypatch.setattr(
        "travel_agent.runtime.MockWeatherProvider", FailingWeatherProvider
    )
    runtime = await PlanningRuntime.create(Settings.from_env({}))
    try:
        created = await runtime.create_plan_session(
            PlanningRequest(trip=hangzhou_trip), session_id="weather-failure-session"
        )
        active = await runtime.resume_plan_session(
            LifecycleResumeRequest(
                interrupt_id=created.interrupt.id,
                request_id=uuid4(),
                expected_session_revision=0,
                action={"kind": "accept_recommendation"},
            ),
            session_id="weather-failure-session",
        )
        with pytest.raises(WeatherUnavailableError) as raised:
            await runtime.refresh_plan_weather(
                WeatherRefreshRequest(
                    request_id=uuid4(),
                    expected_active_version_id="V1",
                    expected_session_revision=active.session_revision,
                ),
                session_id="weather-failure-session",
            )
        assert raised.value.result.error.code == "weather_upstream_unavailable"

        stored = await runtime.plan_repository.get("weather-failure-session")
        assert stored.active_version_id == "V1"
        assert stored.status.value == "active"
        assert stored.weather_monitor.last_outcome.value == "provider_failed"
        assert stored.weather_monitor.last_safe_error_code == "weather_upstream_unavailable"
    finally:
        await runtime.close()
