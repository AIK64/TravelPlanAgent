from __future__ import annotations

import logging
from time import perf_counter
from typing import Literal
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from travel_agent.domain.models import (
    PlanCandidate,
    PlanningRequest,
    PlanningResponse,
    POIResolutionIssue,
    TripSpec,
    ValidationStatus,
)
from travel_agent.domain.tool_models import (
    POIFacts,
    ToolCallContext,
    ToolExecutionSummary,
    ToolResult,
    ToolStatus,
)
from travel_agent.graph.state import TravelState
from travel_agent.planning.defaults import POIDefaultPolicy
from travel_agent.planning.drafts import (
    collect_route_queries,
    prepare_candidate_drafts,
)
from travel_agent.planning.planner import materialize_candidates
from travel_agent.planning.search_plan import build_search_plan as create_search_plan
from travel_agent.planning.validator import validate_candidate
from travel_agent.tools.errors import ToolUnavailableError
from travel_agent.tools.gateway import ToolGateway


logger = logging.getLogger(__name__)
_DELIVERABLE_STATUSES = {
    ValidationStatus.VALID,
    ValidationStatus.VALID_WITH_WARNINGS,
}
_CHECKPOINT_ALLOWED_TYPES = (
    ("travel_agent.domain.models", "ItemType"),
    ("travel_agent.domain.models", "Pace"),
    ("travel_agent.domain.models", "PlanCandidate"),
    ("travel_agent.domain.models", "PlanStyle"),
    ("travel_agent.domain.models", "PlanningPOI"),
    ("travel_agent.domain.models", "POIResolutionIssue"),
    ("travel_agent.domain.models", "TripSpec"),
    ("travel_agent.domain.models", "ValidationStatus"),
    ("travel_agent.domain.models", "ViolationSeverity"),
    ("travel_agent.domain.tool_models", "POIFacts"),
    ("travel_agent.domain.tool_models", "POISearchQuery"),
    ("travel_agent.domain.tool_models", "RouteMode"),
    ("travel_agent.domain.tool_models", "RouteQuery"),
    ("travel_agent.domain.tool_models", "RouteResult"),
    ("travel_agent.domain.tool_models", "ToolExecutionSummary"),
    ("travel_agent.domain.tool_models", "ToolStatus"),
    ("travel_agent.domain.tool_models", "ValueSource"),
    ("travel_agent.planning.drafts", "CandidateDraft"),
)


def _log_node_started(state: TravelState, node: str) -> None:
    logger.info(
        "node.started | thread_id=%s node=%s iteration=%s status=%s",
        state["thread_id"],
        node,
        state["iterations"],
        state["status"],
    )


def _log_node_completed(
    state: TravelState,
    node: str,
    next_status: str,
    **details: object,
) -> None:
    detail_text = " ".join(f"{key}={value}" for key, value in details.items())
    logger.info(
        "node.completed | thread_id=%s node=%s iteration=%s status=%s%s",
        state["thread_id"],
        node,
        state["iterations"],
        next_status,
        f" {detail_text}" if detail_text else "",
    )


def _log_candidates(state: TravelState, candidates: list[PlanCandidate]) -> None:
    phase = "initial" if state["iterations"] == 0 else "replan"
    for candidate in candidates:
        activity_count = sum(len(day.items) for day in candidate.days)
        logger.info(
            "candidate.generated | thread_id=%s phase=%s candidate_id=%s "
            "style=%s activities=%s cost=%s travel_minutes=%s score=%s",
            state["thread_id"],
            phase,
            candidate.id,
            candidate.style.value,
            activity_count,
            candidate.metrics.estimated_cost,
            candidate.metrics.total_travel_minutes,
            candidate.score,
        )
        for day in candidate.days:
            poi_names = ",".join(item.name for item in day.items) or "none"
            timeline = ",".join(
                f"{item.name}[{item.start_at:%H:%M}-{item.end_at:%H:%M}]"
                for item in day.items
            ) or "none"
            logger.debug(
                "candidate.schedule | thread_id=%s candidate_id=%s style=%s "
                "day=%s poi_names=%s timeline=%s cost=%s travel_minutes=%s "
                "walking_meters=%s fatigue=%s",
                state["thread_id"],
                candidate.id,
                candidate.style.value,
                day.date,
                poi_names,
                timeline,
                day.estimated_cost,
                day.total_travel_minutes,
                day.walking_distance_meters,
                day.fatigue_score,
            )


def _tool_summary(
    result: ToolResult[object],
    operation: str,
) -> ToolExecutionSummary:
    return ToolExecutionSummary(
        provider=result.provider,
        operation=operation,
        status=result.status,
        cache_hit=result.cache_hit,
        attempt_count=result.attempt_count,
    )


def _normalize(value: str) -> str:
    return value.strip().casefold()


def _is_required(facts: POIFacts, trip: TripSpec) -> bool:
    poi_name = _normalize(facts.name)
    return any(
        _normalize(required) in poi_name or poi_name in _normalize(required)
        for required in trip.must_visit
    )


def _deliverable_candidates(state: TravelState) -> list[PlanCandidate]:
    return [
        candidate
        for candidate in state["candidates"]
        if candidate.validation is not None
        and candidate.validation.status in _DELIVERABLE_STATUSES
    ]


def select_best_candidate(candidates: list[PlanCandidate]) -> PlanCandidate:
    """优先选择完全合法候选，再在同一验证等级内比较分数。"""
    return min(
        candidates,
        key=lambda candidate: (
            0
            if candidate.validation is not None
            and candidate.validation.status is ValidationStatus.VALID
            else 1,
            -(
                candidate.score
                if candidate.score is not None
                else float("-inf")
            ),
            candidate.id,
        ),
    )


def route_after_validation(
    state: TravelState,
) -> Literal["select_best", "replan", "mark_infeasible"]:
    deliverable = _deliverable_candidates(state)
    if deliverable:
        next_node = "select_best"
    elif (
        state["planning_pois"]
        and state["iterations"] < state["max_replan_rounds"]
    ):
        next_node = "replan"
    else:
        next_node = "mark_infeasible"
    logger.info(
        "routing.decision | thread_id=%s after=validate_candidates next=%s "
        "iteration=%s deliverable_candidates=%s max_replan_rounds=%s",
        state["thread_id"],
        next_node,
        state["iterations"],
        len(deliverable),
        state["max_replan_rounds"],
    )
    return next_node


def build_workflow(
    gateway: ToolGateway,
    defaults: POIDefaultPolicy,
) -> CompiledStateGraph:
    """构建只通过注入 ToolGateway 获取外部事实的异步规划图。"""

    def build_search_plan(state: TravelState) -> dict:
        _log_node_started(state, "build_search_plan")
        queries = create_search_plan(state["trip"])
        logger.info(
            "search_plan.created | thread_id=%s query_count=%s priorities=%s",
            state["thread_id"],
            len(queries),
            ",".join(str(query.priority) for query in queries) or "none",
        )
        _log_node_completed(
            state,
            "build_search_plan",
            "search_planned",
            query_count=len(queries),
        )
        return {"search_queries": queries, "status": "search_planned"}

    async def load_pois(state: TravelState) -> dict:
        _log_node_started(state, "load_pois")
        results = await gateway.search_pois(
            state["search_queries"],
            ToolCallContext(thread_id=state["thread_id"]),
        )
        for result in results:
            if result.status is ToolStatus.FAILED:
                raise ToolUnavailableError.from_result(result, state["thread_id"])

        ordered = sorted(
            enumerate(zip(state["search_queries"], results, strict=True)),
            key=lambda item: (-item[1][0].priority, item[0]),
        )
        facts_by_id: dict[str, POIFacts] = {}
        for _, (_, result) in ordered:
            assert result.data is not None
            for facts in result.data:
                facts_by_id.setdefault(facts.id, facts)
                if len(facts_by_id) == 12:
                    break
            if len(facts_by_id) == 12:
                break

        poi_facts = list(facts_by_id.values())
        summaries = [
            _tool_summary(result, "poi.search")
            for result in results
        ]
        logger.info(
            "poi_facts.loaded | thread_id=%s fact_count=%s",
            state["thread_id"],
            len(poi_facts),
        )
        _log_node_completed(
            state,
            "load_pois",
            "poi_facts_loaded",
            fact_count=len(poi_facts),
        )
        return {
            "poi_facts": poi_facts,
            "tool_summaries": [*state["tool_summaries"], *summaries],
            "status": "poi_facts_loaded",
        }

    def resolve_poi_facts(state: TravelState) -> dict:
        _log_node_started(state, "resolve_poi_facts")
        planning_pois = []
        issues = []
        for facts in state["poi_facts"]:
            resolution = defaults.resolve(facts, state["trip"])
            if resolution.poi is not None:
                planning_pois.append(resolution.poi)
            else:
                issues.append(
                    POIResolutionIssue(
                        poi_id=facts.id,
                        poi_name=facts.name,
                        missing_fields=resolution.missing_fields,
                        required=_is_required(facts, state["trip"]),
                    )
                )
        logger.info(
            "poi_context.loaded | thread_id=%s fact_count=%s planning_poi_count=%s "
            "resolution_issue_count=%s",
            state["thread_id"],
            len(state["poi_facts"]),
            len(planning_pois),
            len(issues),
        )
        _log_node_completed(
            state,
            "resolve_poi_facts",
            "poi_context_loaded",
            poi_count=len(planning_pois),
        )
        return {
            "planning_pois": planning_pois,
            "poi_resolution_issues": issues,
            "status": "poi_context_loaded",
        }

    def prepare_drafts(state: TravelState) -> dict:
        _log_node_started(state, "prepare_candidate_drafts")
        drafts = prepare_candidate_drafts(
            state["trip"],
            state["planning_pois"],
            replan_round=state["iterations"],
        )
        logger.info(
            "candidate_drafts.prepared | thread_id=%s iteration=%s draft_count=%s",
            state["thread_id"],
            state["iterations"],
            len(drafts),
        )
        _log_node_completed(
            state,
            "prepare_candidate_drafts",
            "candidate_drafts_prepared",
            draft_count=len(drafts),
        )
        return {
            "candidate_drafts": drafts,
            "status": "candidate_drafts_prepared",
        }

    async def load_routes(state: TravelState) -> dict:
        _log_node_started(state, "load_routes")
        queries = collect_route_queries(
            state["trip"],
            state["candidate_drafts"],
            state["planning_pois"],
        )
        results = await gateway.get_routes(
            queries,
            ToolCallContext(thread_id=state["thread_id"]),
        )
        for result in results.values():
            if result.status is ToolStatus.FAILED:
                raise ToolUnavailableError.from_result(result, state["thread_id"])
        routes = {}
        for key, result in results.items():
            assert result.data is not None
            routes[key] = result.data
        summaries = [
            _tool_summary(result, "route.get_driving")
            for result in results.values()
        ]
        logger.info(
            "routes.loaded | thread_id=%s iteration=%s query_count=%s route_count=%s",
            state["thread_id"],
            state["iterations"],
            len(queries),
            len(routes),
        )
        _log_node_completed(
            state,
            "load_routes",
            "routes_loaded",
            route_count=len(routes),
        )
        return {
            "route_queries": queries,
            "route_results": routes,
            "tool_summaries": [*state["tool_summaries"], *summaries],
            "status": "routes_loaded",
        }

    def materialize(state: TravelState) -> dict:
        _log_node_started(state, "materialize_candidates")
        candidates = materialize_candidates(
            state["trip"],
            state["candidate_drafts"],
            state["planning_pois"],
            state["route_results"],
        )
        _log_candidates(state, candidates)
        _log_node_completed(
            state,
            "materialize_candidates",
            "candidates_materialized",
            candidate_count=len(candidates),
        )
        return {"candidates": candidates, "status": "candidates_materialized"}

    def validate(state: TravelState) -> dict:
        _log_node_started(state, "validate_candidates")
        validated = []
        for candidate in state["candidates"]:
            result = validate_candidate(
                state["trip"], candidate, state["planning_pois"]
            )
            validated_candidate = candidate.model_copy(
                update={"validation": result}
            )
            validated.append(validated_candidate)
            violation_types = ",".join(
                item.type for item in result.violations
            ) or "none"
            logger.info(
                "candidate.validated | thread_id=%s candidate_id=%s style=%s "
                "status=%s valid=%s violation_count=%s violation_types=%s",
                state["thread_id"],
                candidate.id,
                candidate.style.value,
                result.status.value,
                str(result.valid).lower(),
                len(result.violations),
                violation_types,
            )
            for violation in result.violations:
                logger.debug(
                    "candidate.violation | thread_id=%s candidate_id=%s type=%s "
                    "severity=%s day=%s message=%s repair_hint=%s",
                    state["thread_id"],
                    candidate.id,
                    violation.type,
                    violation.severity.value,
                    violation.day or "none",
                    violation.message,
                    violation.repair_hint or "none",
                )
        deliverable_count = sum(
            candidate.validation is not None
            and candidate.validation.status in _DELIVERABLE_STATUSES
            for candidate in validated
        )
        _log_node_completed(
            state,
            "validate_candidates",
            "validated",
            candidate_count=len(validated),
            deliverable_count=deliverable_count,
        )
        return {"candidates": validated, "status": "validated"}

    def replan(state: TravelState) -> dict:
        _log_node_started(state, "replan")
        next_iteration = state["iterations"] + 1
        logger.info(
            "replan.started | thread_id=%s iteration=%s strategy=reduce_density_low_cost",
            state["thread_id"],
            next_iteration,
        )
        logger.info(
            "replan.completed | thread_id=%s iteration=%s",
            state["thread_id"],
            next_iteration,
        )
        return {
            "iterations": next_iteration,
            "status": "replanning",
            "message": (
                f"第 {next_iteration} 轮重规划：降低活动密度并优先低成本地点"
            ),
        }

    def select_best(state: TravelState) -> dict:
        _log_node_started(state, "select_best")
        deliverable = _deliverable_candidates(state)
        selected = select_best_candidate(deliverable)
        logger.info(
            "plan.selected | thread_id=%s candidate_id=%s style=%s "
            "validation_status=%s score=%s cost=%s travel_minutes=%s",
            state["thread_id"],
            selected.id,
            selected.style.value,
            selected.validation.status.value if selected.validation else "none",
            selected.score,
            selected.metrics.estimated_cost,
            selected.metrics.total_travel_minutes,
        )
        _log_node_completed(state, "select_best", "completed", selected=selected.id)
        return {
            "selected_plan": selected,
            "status": "completed",
            "message": f"已选择 {selected.style.value} 方案",
        }

    def mark_infeasible(state: TravelState) -> dict:
        _log_node_started(state, "mark_infeasible")
        logger.warning(
            "planning.infeasible | thread_id=%s iterations=%s candidate_count=%s",
            state["thread_id"],
            state["iterations"],
            len(state["candidates"]),
        )
        _log_node_completed(state, "mark_infeasible", "infeasible")
        return {
            "selected_plan": None,
            "status": "infeasible",
            "message": "在当前约束和重规划预算内没有找到合法方案",
        }

    builder = StateGraph(TravelState)
    builder.add_node("build_search_plan", build_search_plan)
    builder.add_node("load_pois", load_pois)
    builder.add_node("resolve_poi_facts", resolve_poi_facts)
    builder.add_node("prepare_candidate_drafts", prepare_drafts)
    builder.add_node("load_routes", load_routes)
    builder.add_node("materialize_candidates", materialize)
    builder.add_node("validate_candidates", validate)
    builder.add_node("replan", replan)
    builder.add_node("select_best", select_best)
    builder.add_node("mark_infeasible", mark_infeasible)

    builder.add_edge(START, "build_search_plan")
    builder.add_edge("build_search_plan", "load_pois")
    builder.add_edge("load_pois", "resolve_poi_facts")
    builder.add_edge("resolve_poi_facts", "prepare_candidate_drafts")
    builder.add_edge("prepare_candidate_drafts", "load_routes")
    builder.add_edge("load_routes", "materialize_candidates")
    builder.add_edge("materialize_candidates", "validate_candidates")
    builder.add_conditional_edges("validate_candidates", route_after_validation)
    builder.add_edge("replan", "prepare_candidate_drafts")
    builder.add_edge("select_best", END)
    builder.add_edge("mark_infeasible", END)
    checkpoint_serde = JsonPlusSerializer(
        allowed_msgpack_modules=_CHECKPOINT_ALLOWED_TYPES
    )
    return builder.compile(checkpointer=InMemorySaver(serde=checkpoint_serde))


def initial_state(request: PlanningRequest, thread_id: str) -> TravelState:
    return {
        "thread_id": thread_id,
        "trip": request.trip,
        "search_queries": [],
        "poi_facts": [],
        "planning_pois": [],
        "poi_resolution_issues": [],
        "candidate_drafts": [],
        "route_queries": [],
        "route_results": {},
        "tool_summaries": [],
        "candidates": [],
        "selected_plan": None,
        "iterations": 0,
        "max_replan_rounds": request.max_replan_rounds,
        "status": "started",
        "message": None,
    }


def response_from_state(state: TravelState) -> PlanningResponse:
    return PlanningResponse(
        status=state["status"],
        selected_plan=state["selected_plan"],
        candidates=state["candidates"],
        iterations=state["iterations"],
        message=state["message"],
    )


async def run_planning(
    workflow: CompiledStateGraph,
    request: PlanningRequest,
    thread_id: str | None = None,
) -> PlanningResponse:
    run_thread_id = thread_id or str(uuid4())
    started_at = perf_counter()
    logger.info(
        "planning.started | thread_id=%s destination=%s days=%s travelers=%s "
        "max_replan_rounds=%s",
        run_thread_id,
        request.trip.destination,
        request.trip.day_count,
        request.trip.travelers,
        request.max_replan_rounds,
    )
    try:
        result = await workflow.ainvoke(
            initial_state(request, run_thread_id),
            config={
                "configurable": {"thread_id": run_thread_id},
                "recursion_limit": 35,
            },
        )
    except Exception:
        logger.exception("planning.failed | thread_id=%s", run_thread_id)
        raise
    elapsed_ms = round((perf_counter() - started_at) * 1000, 2)
    selected_id = result["selected_plan"].id if result["selected_plan"] else "none"
    logger.info(
        "planning.completed | thread_id=%s status=%s selected=%s "
        "iterations=%s elapsed_ms=%s",
        run_thread_id,
        result["status"],
        selected_id,
        result["iterations"],
        elapsed_ms,
    )
    return response_from_state(result)
