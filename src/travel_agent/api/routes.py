from __future__ import annotations

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Response

from travel_agent import __version__
from travel_agent.api.dependencies import get_runtime
from travel_agent.domain.models import PlanningRequest, PlanningResponse
from travel_agent.domain.lifecycle_models import (
    LifecycleResumeRequest,
    PlanDiff,
    PlanSessionResponse,
    PlanVersion,
)
from travel_agent.runtime import PlanningRuntime
from travel_agent.execution.models import AgentRunRecord, TracePage
from travel_agent.domain.weather_models import (
    WeatherEventView,
    WeatherRefreshRequest,
    WeatherStateView,
)
from travel_agent.requirements.models import (
    ClarificationResumeRequest,
    NaturalPlanningRequest,
    NaturalPlanningResponse,
)


router = APIRouter()


@router.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.post("/api/v1/plans", response_model=PlanningResponse, tags=["planning"])
async def create_plan(
    request: PlanningRequest,
    response: Response,
    runtime: Annotated[PlanningRuntime, Depends(get_runtime)],
) -> PlanningResponse:
    thread_id = str(uuid4())
    return await _execute_with_run(
        runtime,
        "execute_plan",
        "plan",
        response,
        request,
        thread_id=thread_id,
    )


@router.post(
    "/api/v1/plans/from-text",
    response_model=NaturalPlanningResponse,
    tags=["planning"],
)
async def create_plan_from_text(
    request: NaturalPlanningRequest,
    response: Response,
    runtime: Annotated[PlanningRuntime, Depends(get_runtime)],
) -> NaturalPlanningResponse:
    thread_id = str(uuid4())
    return await _execute_with_run(
        runtime,
        "execute_plan_from_text",
        "plan_from_text",
        response,
        request,
        thread_id=thread_id,
    )


@router.post(
    "/api/v1/plans/from-text/{thread_id}/resume",
    response_model=NaturalPlanningResponse,
    tags=["planning"],
)
async def resume_plan_from_text(
    thread_id: str,
    request: ClarificationResumeRequest,
    response: Response,
    runtime: Annotated[PlanningRuntime, Depends(get_runtime)],
) -> NaturalPlanningResponse:
    return await _execute_with_run(
        runtime,
        "execute_resume_from_text",
        "resume_from_text",
        response,
        request,
        thread_id=thread_id,
    )


@router.post(
    "/api/v1/plan-sessions",
    response_model=PlanSessionResponse,
    tags=["plan-lifecycle"],
)
async def create_plan_session(
    request: PlanningRequest,
    response: Response,
    runtime: Annotated[PlanningRuntime, Depends(get_runtime)],
) -> PlanSessionResponse:
    session_id = str(uuid4())
    return await _execute_with_run(
        runtime,
        "execute_create_plan_session",
        "create_plan_session",
        response,
        request,
        session_id=session_id,
    )


@router.post(
    "/api/v1/plan-sessions/from-text",
    response_model=PlanSessionResponse,
    tags=["plan-lifecycle"],
)
async def create_plan_session_from_text(
    request: NaturalPlanningRequest,
    response: Response,
    runtime: Annotated[PlanningRuntime, Depends(get_runtime)],
) -> PlanSessionResponse:
    return await _execute_with_run(
        runtime,
        "execute_create_plan_session_from_text",
        "create_plan_session_from_text",
        response,
        request,
        session_id=str(uuid4()),
    )


@router.post(
    "/api/v1/plan-sessions/{session_id}/resume",
    response_model=PlanSessionResponse,
    tags=["plan-lifecycle"],
)
async def resume_plan_session(
    session_id: str,
    request: LifecycleResumeRequest,
    response: Response,
    runtime: Annotated[PlanningRuntime, Depends(get_runtime)],
) -> PlanSessionResponse:
    return await _execute_with_run(
        runtime,
        "execute_resume_plan_session",
        "resume_plan_session",
        response,
        request,
        session_id=session_id,
    )


@router.get(
    "/api/v1/plan-sessions/{session_id}",
    response_model=PlanSessionResponse,
    tags=["plan-lifecycle"],
)
async def get_plan_session(
    session_id: str,
    runtime: Annotated[PlanningRuntime, Depends(get_runtime)],
) -> PlanSessionResponse:
    return await runtime.get_plan_session(session_id=session_id)


@router.get(
    "/api/v1/plan-sessions/{session_id}/versions",
    response_model=list[PlanVersion],
    tags=["plan-lifecycle"],
)
async def get_plan_versions(
    session_id: str,
    runtime: Annotated[PlanningRuntime, Depends(get_runtime)],
):
    return await runtime.get_plan_versions(session_id=session_id)


@router.get(
    "/api/v1/plan-sessions/{session_id}/versions/{version_id}",
    response_model=PlanVersion,
    tags=["plan-lifecycle"],
)
async def get_plan_version(
    session_id: str,
    version_id: str,
    runtime: Annotated[PlanningRuntime, Depends(get_runtime)],
):
    return await runtime.get_plan_version(
        session_id=session_id, version_id=version_id
    )


@router.get(
    "/api/v1/plan-sessions/{session_id}/diff",
    response_model=PlanDiff,
    tags=["plan-lifecycle"],
)
async def get_plan_diff(
    session_id: str,
    from_id: str,
    to_id: str,
    runtime: Annotated[PlanningRuntime, Depends(get_runtime)],
):
    return await runtime.get_plan_diff(
        session_id=session_id, from_id=from_id, to_id=to_id
    )


@router.post(
    "/api/v1/plan-sessions/{session_id}/weather/refresh",
    response_model=PlanSessionResponse,
    tags=["weather-lifecycle"],
)
async def refresh_plan_weather(
    session_id: str,
    request: WeatherRefreshRequest,
    response: Response,
    runtime: Annotated[PlanningRuntime, Depends(get_runtime)],
) -> PlanSessionResponse:
    return await _execute_with_run(
        runtime,
        "execute_refresh_plan_weather",
        "refresh_plan_weather",
        response,
        request,
        session_id=session_id,
    )


@router.get(
    "/api/v1/plan-sessions/{session_id}/weather",
    response_model=WeatherStateView,
    tags=["weather-lifecycle"],
)
async def get_plan_weather(
    session_id: str,
    runtime: Annotated[PlanningRuntime, Depends(get_runtime)],
) -> WeatherStateView:
    return await runtime.get_plan_weather(session_id=session_id)


@router.get(
    "/api/v1/plan-sessions/{session_id}/weather/events",
    response_model=list[WeatherEventView],
    tags=["weather-lifecycle"],
)
async def get_plan_weather_events(
    session_id: str,
    runtime: Annotated[PlanningRuntime, Depends(get_runtime)],
):
    return await runtime.get_plan_weather_events(session_id=session_id)


@router.get(
    "/api/v1/plan-sessions/{session_id}/weather/events/{event_id}",
    response_model=WeatherEventView,
    tags=["weather-lifecycle"],
)
async def get_plan_weather_event(
    session_id: str,
    event_id: str,
    runtime: Annotated[PlanningRuntime, Depends(get_runtime)],
):
    return await runtime.get_plan_weather_event(
        session_id=session_id, event_id=event_id
    )


@router.get(
    "/api/v1/runs/{run_id}",
    response_model=AgentRunRecord,
    tags=["agent-runs"],
)
async def get_agent_run(
    run_id: str,
    runtime: Annotated[PlanningRuntime, Depends(get_runtime)],
) -> AgentRunRecord:
    return await runtime.get_agent_run(run_id)


@router.get(
    "/api/v1/runs/{run_id}/trace",
    response_model=TracePage,
    tags=["agent-runs"],
)
async def get_agent_trace(
    run_id: str,
    runtime: Annotated[PlanningRuntime, Depends(get_runtime)],
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> TracePage:
    events = await runtime.get_agent_trace(
        run_id, after_sequence=after_sequence, limit=limit
    )
    return TracePage(
        run_id=run_id,
        events=events,
        next_sequence=events[-1].sequence if len(events) == limit else None,
    )


@router.get(
    "/api/v1/plan-sessions/{session_id}/runs",
    response_model=list[AgentRunRecord],
    tags=["agent-runs"],
)
async def get_session_runs(
    session_id: str,
    runtime: Annotated[PlanningRuntime, Depends(get_runtime)],
    limit: int = Query(default=50, ge=1, le=100),
):
    return await runtime.get_session_runs(session_id, limit=limit)


@router.get(
    "/api/v1/requirement-threads/{thread_id}/runs",
    response_model=list[AgentRunRecord],
    tags=["agent-runs"],
)
async def get_thread_runs(
    thread_id: str,
    runtime: Annotated[PlanningRuntime, Depends(get_runtime)],
    limit: int = Query(default=50, ge=1, le=100),
):
    return await runtime.get_thread_runs(thread_id, limit=limit)


async def _execute_with_run(
    runtime: object,
    execute_method: str,
    legacy_method: str,
    response: Response,
    *args,
    **kwargs,
):
    execute = getattr(runtime, execute_method, None)
    if execute is None:
        return await getattr(runtime, legacy_method)(*args, **kwargs)
    result = await execute(*args, **kwargs)
    run = getattr(result, "run", None)
    if run is not None:
        response.headers["X-Agent-Run-Id"] = run.run_id
        response.headers["X-Agent-Trace-Status"] = run.trace_status.value
    return result.payload

