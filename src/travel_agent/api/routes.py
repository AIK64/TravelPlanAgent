from __future__ import annotations

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends

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
    runtime: Annotated[PlanningRuntime, Depends(get_runtime)],
) -> PlanningResponse:
    return await runtime.plan(request, thread_id=str(uuid4()))


@router.post(
    "/api/v1/plans/from-text",
    response_model=NaturalPlanningResponse,
    tags=["planning"],
)
async def create_plan_from_text(
    request: NaturalPlanningRequest,
    runtime: Annotated[PlanningRuntime, Depends(get_runtime)],
) -> NaturalPlanningResponse:
    return await runtime.plan_from_text(request, thread_id=str(uuid4()))


@router.post(
    "/api/v1/plans/from-text/{thread_id}/resume",
    response_model=NaturalPlanningResponse,
    tags=["planning"],
)
async def resume_plan_from_text(
    thread_id: str,
    request: ClarificationResumeRequest,
    runtime: Annotated[PlanningRuntime, Depends(get_runtime)],
) -> NaturalPlanningResponse:
    return await runtime.resume_from_text(request, thread_id=thread_id)


@router.post(
    "/api/v1/plan-sessions",
    response_model=PlanSessionResponse,
    tags=["plan-lifecycle"],
)
async def create_plan_session(
    request: PlanningRequest,
    runtime: Annotated[PlanningRuntime, Depends(get_runtime)],
) -> PlanSessionResponse:
    return await runtime.create_plan_session(request, session_id=str(uuid4()))


@router.post(
    "/api/v1/plan-sessions/from-text",
    response_model=PlanSessionResponse,
    tags=["plan-lifecycle"],
)
async def create_plan_session_from_text(
    request: NaturalPlanningRequest,
    runtime: Annotated[PlanningRuntime, Depends(get_runtime)],
) -> PlanSessionResponse:
    return await runtime.create_plan_session_from_text(
        request, session_id=str(uuid4())
    )


@router.post(
    "/api/v1/plan-sessions/{session_id}/resume",
    response_model=PlanSessionResponse,
    tags=["plan-lifecycle"],
)
async def resume_plan_session(
    session_id: str,
    request: LifecycleResumeRequest,
    runtime: Annotated[PlanningRuntime, Depends(get_runtime)],
) -> PlanSessionResponse:
    return await runtime.resume_plan_session(request, session_id=session_id)


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
    runtime: Annotated[PlanningRuntime, Depends(get_runtime)],
) -> PlanSessionResponse:
    return await runtime.refresh_plan_weather(request, session_id=session_id)


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

