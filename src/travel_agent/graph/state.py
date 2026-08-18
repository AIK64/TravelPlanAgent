from __future__ import annotations

from typing import TypedDict

from travel_agent.domain.models import PlanCandidate, POI, TripSpec


class TravelState(TypedDict):
    trip: TripSpec
    pois: list[POI]
    candidates: list[PlanCandidate]
    selected_plan: PlanCandidate | None
    iterations: int
    max_replan_rounds: int
    status: str
    message: str | None

