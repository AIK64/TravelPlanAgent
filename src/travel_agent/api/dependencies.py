from __future__ import annotations

from fastapi import Request

from travel_agent.runtime import PlanningRuntime


def get_runtime(request: Request) -> PlanningRuntime:
    return request.app.state.planning_runtime
