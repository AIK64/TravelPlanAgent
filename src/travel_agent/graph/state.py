from __future__ import annotations

from typing import TypedDict

from travel_agent.domain.models import (
    PlanCandidate,
    PlanningPOI,
    POIResolutionIssue,
    TripSpec,
)
from travel_agent.domain.tool_models import (
    POIFacts,
    POISearchQuery,
    RouteQuery,
    RouteResult,
    ToolExecutionSummary,
)
from travel_agent.planning.drafts import CandidateDraft


class TravelState(TypedDict):
    thread_id: str
    trip: TripSpec
    search_queries: list[POISearchQuery]
    poi_facts: list[POIFacts]
    planning_pois: list[PlanningPOI]
    poi_resolution_issues: list[POIResolutionIssue]
    candidate_drafts: list[CandidateDraft]
    route_queries: list[RouteQuery]
    route_results: dict[str, RouteResult]
    tool_summaries: list[ToolExecutionSummary]
    candidates: list[PlanCandidate]
    selected_plan: PlanCandidate | None
    iterations: int
    pending_replan_round: int | None
    max_replan_rounds: int
    status: str
    message: str | None
