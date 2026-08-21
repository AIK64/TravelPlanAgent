from __future__ import annotations

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends

from travel_agent import __version__
from travel_agent.api.dependencies import get_runtime
from travel_agent.domain.models import PlanningRequest, PlanningResponse
from travel_agent.runtime import PlanningRuntime


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

