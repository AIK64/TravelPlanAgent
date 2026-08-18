from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter

from travel_agent import __version__
from travel_agent.domain.models import PlanningRequest, PlanningResponse
from travel_agent.graph.workflow import run_planning


router = APIRouter()


@router.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.post("/api/v1/plans", response_model=PlanningResponse, tags=["planning"])
def create_plan(request: PlanningRequest) -> PlanningResponse:
    return run_planning(request, thread_id=str(uuid4()))

