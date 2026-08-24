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
from travel_agent.domain.optimization_models import (
    OptimizationProblem,
    OptimizationResult,
)
from travel_agent.domain.repair_models import CriticReport, RepairAttempt, RepairPlan
from travel_agent.planning.drafts import CandidateDraft


class TravelState(TypedDict):
    thread_id: str
    trip: TripSpec
    search_queries: list[POISearchQuery]
    poi_facts: list[POIFacts]
    planning_pois: list[PlanningPOI]
    optimization_pois: list[PlanningPOI]
    optimization_problem: OptimizationProblem | None
    optimization_result: OptimizationResult | None
    route_matrix_cache_hits: int
    route_matrix_provider_calls: int
    poi_resolution_issues: list[POIResolutionIssue]
    candidate_drafts: list[CandidateDraft]
    route_queries: list[RouteQuery]
    delta_route_queries: list[RouteQuery]
    route_results: dict[str, RouteResult]
    reused_route_keys: list[str]
    tool_summaries: list[ToolExecutionSummary]
    candidates: list[PlanCandidate]
    selected_plan: PlanCandidate | None
    iterations: int
    pending_replan_round: int | None
    max_replan_rounds: int
    repair_target_candidate_id: str | None
    critic_report: CriticReport | None
    repair_plan: RepairPlan | None
    repair_history: list[RepairAttempt]
    preserved_day_hashes: dict[str, str]
    repair_terminal_reason: str | None
    last_route_loaded_count: int
    last_route_reused_count: int
    status: str
    message: str | None
