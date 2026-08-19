from __future__ import annotations

import logging
from time import perf_counter
from typing import Literal
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from travel_agent.domain.models import PlanningRequest, PlanningResponse
from travel_agent.graph.state import TravelState
from travel_agent.planning.mock_data import get_mock_pois
from travel_agent.planning.planner import generate_candidates
from travel_agent.planning.validator import validate_candidate


logger = logging.getLogger(__name__)


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


def _log_candidates(
    state: TravelState,
    candidates: list,
    phase: str,
) -> None:
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


def load_context(state: TravelState) -> dict:
    _log_node_started(state, "load_context")
    pois = get_mock_pois(state["trip"].destination)
    status = "context_loaded" if pois else "missing_context"
    logger.info(
        "context.loaded | thread_id=%s destination=%s poi_count=%s source=mock",
        state["thread_id"],
        state["trip"].destination,
        len(pois),
    )
    _log_node_completed(state, "load_context", status, poi_count=len(pois))
    return {
        "pois": pois,
        "status": status,
        "message": None if pois else "当前 Mock 数据仅支持杭州",
    }


def create_initial_candidates(state: TravelState) -> dict:
    _log_node_started(state, "create_initial_candidates")
    if not state["pois"]:
        _log_node_completed(
            state, "create_initial_candidates", "candidates_created", candidate_count=0
        )
        return {"candidates": []}
    candidates = generate_candidates(
        state["trip"], state["pois"], replan_round=state["iterations"]
    )
    _log_candidates(state, candidates, phase="initial")
    _log_node_completed(
        state,
        "create_initial_candidates",
        "candidates_created",
        candidate_count=len(candidates),
    )
    return {"candidates": candidates, "status": "candidates_created"}


def validate_candidates(state: TravelState) -> dict:
    _log_node_started(state, "validate_candidates")
    validated = []
    for candidate in state["candidates"]:
        result = validate_candidate(state["trip"], candidate, state["pois"])
        validated.append(candidate.model_copy(update={"validation": result}))
        violation_types = ",".join(item.type for item in result.violations) or "none"
        logger.info(
            "candidate.validated | thread_id=%s candidate_id=%s style=%s "
            "valid=%s violation_count=%s violation_types=%s",
            state["thread_id"],
            candidate.id,
            candidate.style.value,
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
    valid_count = sum(
        1
        for candidate in validated
        if candidate.validation is not None and candidate.validation.valid
    )
    _log_node_completed(
        state,
        "validate_candidates",
        "validated",
        candidate_count=len(validated),
        valid_count=valid_count,
    )
    return {"candidates": validated, "status": "validated"}


def route_after_validation(
    state: TravelState,
) -> Literal["select_best", "replan", "mark_infeasible"]:
    valid_count = sum(
        1
        for candidate in state["candidates"]
        if candidate.validation is not None and candidate.validation.valid
    )
    if valid_count:
        next_node = "select_best"
    elif state["iterations"] < state["max_replan_rounds"] and state["pois"]:
        next_node = "replan"
    else:
        next_node = "mark_infeasible"
    logger.info(
        "routing.decision | thread_id=%s after=validate_candidates next=%s "
        "iteration=%s valid_candidates=%s max_replan_rounds=%s",
        state["thread_id"],
        next_node,
        state["iterations"],
        valid_count,
        state["max_replan_rounds"],
    )
    return next_node


def replan(state: TravelState) -> dict:
    _log_node_started(state, "replan")
    next_iteration = state["iterations"] + 1
    logger.info(
        "replan.started | thread_id=%s iteration=%s strategy=reduce_density_low_cost",
        state["thread_id"],
        next_iteration,
    )
    candidates = generate_candidates(
        state["trip"], state["pois"], replan_round=next_iteration
    )
    state_for_logs = dict(state)
    state_for_logs["iterations"] = next_iteration
    _log_candidates(state_for_logs, candidates, phase="replan")
    logger.info(
        "replan.completed | thread_id=%s iteration=%s candidate_count=%s",
        state["thread_id"],
        next_iteration,
        len(candidates),
    )
    _log_node_completed(
        state_for_logs,
        "replan",
        "replanning",
        candidate_count=len(candidates),
    )
    return {
        "candidates": candidates,
        "iterations": next_iteration,
        "status": "replanning",
        "message": f"第 {next_iteration} 轮重规划：降低活动密度并优先低成本地点",
    }


def select_best(state: TravelState) -> dict:
    _log_node_started(state, "select_best")
    valid = [
        candidate
        for candidate in state["candidates"]
        if candidate.validation is not None and candidate.validation.valid
    ]
    selected = max(valid, key=lambda candidate: candidate.score or float("-inf"))
    logger.info(
        "plan.selected | thread_id=%s candidate_id=%s style=%s score=%s "
        "cost=%s travel_minutes=%s",
        state["thread_id"],
        selected.id,
        selected.style.value,
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


def build_workflow():
    builder = StateGraph(TravelState)
    builder.add_node("load_context", load_context)
    builder.add_node("create_initial_candidates", create_initial_candidates)
    builder.add_node("validate_candidates", validate_candidates)
    builder.add_node("replan", replan)
    builder.add_node("select_best", select_best)
    builder.add_node("mark_infeasible", mark_infeasible)

    builder.add_edge(START, "load_context")
    builder.add_edge("load_context", "create_initial_candidates")
    builder.add_edge("create_initial_candidates", "validate_candidates")
    builder.add_conditional_edges("validate_candidates", route_after_validation)
    builder.add_edge("replan", "validate_candidates")
    builder.add_edge("select_best", END)
    builder.add_edge("mark_infeasible", END)
    return builder.compile(checkpointer=InMemorySaver())


workflow = build_workflow()


def run_planning(request: PlanningRequest, thread_id: str | None = None) -> PlanningResponse:
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
    initial_state: TravelState = {
        "thread_id": run_thread_id,
        "trip": request.trip,
        "pois": [],
        "candidates": [],
        "selected_plan": None,
        "iterations": 0,
        "max_replan_rounds": request.max_replan_rounds,
        "status": "started",
        "message": None,
    }
    try:
        result = workflow.invoke(
            initial_state,
            config={
                "configurable": {"thread_id": run_thread_id},
                "recursion_limit": 20,
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
    return PlanningResponse(
        status=result["status"],
        selected_plan=result["selected_plan"],
        candidates=result["candidates"],
        iterations=result["iterations"],
        message=result["message"],
    )
