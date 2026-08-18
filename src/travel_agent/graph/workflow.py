from __future__ import annotations

from typing import Literal
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from travel_agent.domain.models import PlanningRequest, PlanningResponse
from travel_agent.graph.state import TravelState
from travel_agent.planning.mock_data import get_mock_pois
from travel_agent.planning.planner import generate_candidates
from travel_agent.planning.validator import validate_candidate


def load_context(state: TravelState) -> dict:
    pois = get_mock_pois(state["trip"].destination)
    return {
        "pois": pois,
        "status": "context_loaded" if pois else "missing_context",
        "message": None if pois else "当前 Mock 数据仅支持杭州",
    }


def create_initial_candidates(state: TravelState) -> dict:
    if not state["pois"]:
        return {"candidates": []}
    return {
        "candidates": generate_candidates(
            state["trip"], state["pois"], replan_round=state["iterations"]
        ),
        "status": "candidates_created",
    }


def validate_candidates(state: TravelState) -> dict:
    validated = []
    for candidate in state["candidates"]:
        result = validate_candidate(state["trip"], candidate, state["pois"])
        validated.append(candidate.model_copy(update={"validation": result}))
    return {"candidates": validated, "status": "validated"}


def route_after_validation(
    state: TravelState,
) -> Literal["select_best", "replan", "mark_infeasible"]:
    if any(
        candidate.validation is not None and candidate.validation.valid
        for candidate in state["candidates"]
    ):
        return "select_best"
    if state["iterations"] < state["max_replan_rounds"] and state["pois"]:
        return "replan"
    return "mark_infeasible"


def replan(state: TravelState) -> dict:
    next_iteration = state["iterations"] + 1
    candidates = generate_candidates(
        state["trip"], state["pois"], replan_round=next_iteration
    )
    return {
        "candidates": candidates,
        "iterations": next_iteration,
        "status": "replanning",
        "message": f"第 {next_iteration} 轮重规划：降低活动密度并优先低成本地点",
    }


def select_best(state: TravelState) -> dict:
    valid = [
        candidate
        for candidate in state["candidates"]
        if candidate.validation is not None and candidate.validation.valid
    ]
    selected = max(valid, key=lambda candidate: candidate.score or float("-inf"))
    return {
        "selected_plan": selected,
        "status": "completed",
        "message": f"已选择 {selected.style.value} 方案",
    }


def mark_infeasible(state: TravelState) -> dict:
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
    initial_state: TravelState = {
        "trip": request.trip,
        "pois": [],
        "candidates": [],
        "selected_plan": None,
        "iterations": 0,
        "max_replan_rounds": request.max_replan_rounds,
        "status": "started",
        "message": None,
    }
    result = workflow.invoke(
        initial_state,
        config={
            "configurable": {"thread_id": run_thread_id},
            "recursion_limit": 20,
        },
    )
    return PlanningResponse(
        status=result["status"],
        selected_plan=result["selected_plan"],
        candidates=result["candidates"],
        iterations=result["iterations"],
        message=result["message"],
    )

