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
from travel_agent.domain.optimization_models import (
    OptimizationBudget,
    OptimizationProblem,
    OptimizationResult,
    OptimizationSolveStatus,
)
from travel_agent.domain.repair_models import RepairAttempt, RepairOutcome
from travel_agent.domain.tool_models import (
    POIFacts,
    RouteQuery,
    ToolCallContext,
    ToolExecutionSummary,
    ToolResult,
    ToolStatus,
)
from travel_agent.graph.state import TravelState
from travel_agent.planning.defaults import POIDefaultPolicy
from travel_agent.planning.drafts import (
    CandidateDraft,
    prepare_candidate_drafts,
)
from travel_agent.planning.critic import (
    analyze_candidate,
    error_violations,
    select_repair_target as choose_repair_target,
    violation_fingerprint,
)
from travel_agent.planning.impact import collect_route_delta, day_fingerprint
from travel_agent.planning.planner import materialize_candidates
from travel_agent.planning.optimization import (
    ORToolsOptimizationSolver,
    OptimizationSolver,
    OptimizationTimeoutError,
    build_optimization_problem as create_optimization_problem,
    collect_route_matrix_queries,
    degraded_result,
    drafts_from_optimization,
    select_optimization_pois,
)
from travel_agent.planning.policy import PlanningPolicy
from travel_agent.planning.repair import apply_repair_plan, build_repair_plan
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
    ("travel_agent.domain.optimization_models", "ObjectiveBreakdown"),
    ("travel_agent.domain.optimization_models", "ObjectiveWeights"),
    ("travel_agent.domain.optimization_models", "OptimizationBudget"),
    ("travel_agent.domain.optimization_models", "OptimizationDayAssignment"),
    ("travel_agent.domain.optimization_models", "OptimizationPOI"),
    ("travel_agent.domain.optimization_models", "OptimizationProblem"),
    ("travel_agent.domain.optimization_models", "OptimizationResult"),
    ("travel_agent.domain.optimization_models", "OptimizationSolution"),
    ("travel_agent.domain.optimization_models", "OptimizationSolveStatus"),
    ("travel_agent.domain.optimization_models", "RouteMatrixEntry"),
    ("travel_agent.domain.repair_models", "CriticReport"),
    ("travel_agent.domain.repair_models", "RepairAction"),
    ("travel_agent.domain.repair_models", "RepairActionKind"),
    ("travel_agent.domain.repair_models", "RepairAttempt"),
    ("travel_agent.domain.repair_models", "RepairOutcome"),
    ("travel_agent.domain.repair_models", "RepairPlan"),
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


def _planning_round(state: TravelState) -> int:
    pending_round = state["pending_replan_round"]
    return pending_round if pending_round is not None else state["iterations"]


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
    phase = "initial" if _planning_round(state) == 0 else "replan"
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
) -> Literal["select_best", "select_repair_target", "mark_infeasible"]:
    deliverable = _deliverable_candidates(state)
    if deliverable:
        next_node = "select_best"
    elif (
        state["repair_terminal_reason"] is None
        and state["planning_pois"]
        and state["iterations"] < state["max_replan_rounds"]
    ):
        next_node = "select_repair_target"
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


def route_after_critic(
    state: TravelState,
) -> Literal["build_repair_plan", "mark_infeasible"]:
    report = state["critic_report"]
    return (
        "build_repair_plan"
        if report is not None and report.repairable
        else "mark_infeasible"
    )


def route_after_repair_plan(
    state: TravelState,
) -> Literal["apply_local_repair", "mark_infeasible"]:
    return (
        "apply_local_repair"
        if state["repair_plan"] is not None
        and state["repair_terminal_reason"] is None
        else "mark_infeasible"
    )


def route_after_delta_routes(
    state: TravelState,
) -> Literal["load_delta_routes", "materialize_candidates"]:
    return (
        "load_delta_routes"
        if state["delta_route_queries"]
        else "materialize_candidates"
    )


def build_workflow(
    gateway: ToolGateway,
    defaults: POIDefaultPolicy,
    policy: PlanningPolicy = PlanningPolicy(),
    *,
    optimizer: OptimizationSolver | None = None,
    optimization_budget: OptimizationBudget = OptimizationBudget(),
) -> CompiledStateGraph:
    """构建只通过注入 ToolGateway 获取外部事实的异步规划图。"""

    def build_search_plan(state: TravelState) -> dict:
        _log_node_started(state, "build_search_plan")
        queries = create_search_plan(
            state["trip"],
            per_query_limit=policy.poi_query_limit,
            max_queries=policy.poi_max_queries,
        )
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
                if len(facts_by_id) == policy.poi_candidate_limit:
                    break
            if len(facts_by_id) == policy.poi_candidate_limit:
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

    active_optimizer = optimizer or ORToolsOptimizationSolver()

    async def build_route_matrix(state: TravelState) -> dict:
        _log_node_started(state, "build_route_matrix")
        optimization_pois = select_optimization_pois(
            state["trip"],
            state["planning_pois"],
            optimization_budget,
        )
        queries = collect_route_matrix_queries(
            state["trip"],
            optimization_pois,
            modes=policy.route_modes,
            strategy=policy.route_strategy,
            max_walking_leg_meters=policy.max_walking_leg_meters,
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
            _tool_summary(
                result,
                f"route.get_{result.data.mode.value}" if result.data else "route.get",
            )
            for result in results.values()
        ]
        cache_hits = sum(result.cache_hit for result in results.values())
        provider_calls = len(results) - cache_hits
        logger.info(
            "route_matrix.loaded | thread_id=%s mode=%s poi_count=%s "
            "query_count=%s route_count=%s cache_hits=%s provider_calls=%s",
            state["thread_id"],
            "+".join(mode.value for mode in policy.route_modes),
            len(optimization_pois),
            len(queries),
            len(routes),
            cache_hits,
            provider_calls,
        )
        _log_node_completed(
            state,
            "build_route_matrix",
            "route_matrix_loaded",
            route_count=len(routes),
        )
        return {
            "optimization_pois": optimization_pois,
            "route_queries": queries,
            "delta_route_queries": [],
            "route_results": routes,
            "reused_route_keys": [],
            "last_route_loaded_count": len(routes),
            "last_route_reused_count": 0,
            "route_matrix_cache_hits": cache_hits,
            "route_matrix_provider_calls": provider_calls,
            "tool_summaries": [*state["tool_summaries"], *summaries],
            "status": "route_matrix_loaded",
        }

    def build_optimization_problem(state: TravelState) -> dict:
        _log_node_started(state, "build_optimization_problem")
        problem = create_optimization_problem(
            state["trip"],
            state["optimization_pois"],
            state["route_results"],
            optimization_budget,
            modes=policy.route_modes,
            strategy=policy.route_strategy,
            max_walking_leg_meters=policy.max_walking_leg_meters,
        )
        logger.info(
            "optimization.problem_built | thread_id=%s problem_id=%s "
            "poi_count=%s route_count=%s day_count=%s mode=%s",
            state["thread_id"],
            problem.id,
            len(problem.pois),
            len(problem.route_matrix),
            len(problem.dates),
            "+".join(mode.value for mode in policy.route_modes),
        )
        _log_node_completed(
            state,
            "build_optimization_problem",
            "optimization_problem_built",
            poi_count=len(problem.pois),
        )
        return {
            "optimization_problem": problem,
            "status": "optimization_problem_built",
        }

    def solve_candidate_variants(state: TravelState) -> dict:
        _log_node_started(state, "solve_candidate_variants")
        problem = state["optimization_problem"]
        if problem is None:
            raise RuntimeError("optimization requires a problem")
        logger.info(
            "optimization.started | thread_id=%s problem_id=%s solver=%s "
            "max_solve_ms=%s max_search_states=%s variants=%s",
            state["thread_id"],
            problem.id,
            getattr(active_optimizer, "name", type(active_optimizer).__name__),
            problem.budget.max_solve_ms,
            problem.budget.max_search_states,
            problem.budget.variant_count,
        )
        degraded_reason: str | None = None
        started = perf_counter()
        try:
            result = active_optimizer.solve(problem)
            if not result.solutions:
                degraded_reason = "optimizer_infeasible"
        except OptimizationTimeoutError:
            result = None
            degraded_reason = "optimizer_timeout"
        if degraded_reason is not None:
            fallback_drafts = prepare_candidate_drafts(
                state["trip"],
                state["optimization_pois"],
                replan_round=0,
            )
            elapsed_ms = round((perf_counter() - started) * 1000, 2)
            result = degraded_result(
                fallback_drafts,
                reason=degraded_reason,
                elapsed_ms=elapsed_ms,
            )
            drafts = fallback_drafts
            logger.warning(
                "optimization.degraded | thread_id=%s problem_id=%s reason=%s "
                "fallback=%s elapsed_ms=%s",
                state["thread_id"],
                problem.id,
                degraded_reason,
                result.solver,
                result.elapsed_ms,
            )
        else:
            assert result is not None
            drafts = drafts_from_optimization(result)
            objective_summary = ",".join(
                f"{solution.style.value}:{solution.objective_value}"
                for solution in result.solutions
            )
            logger.info(
                "optimization.completed | thread_id=%s problem_id=%s status=%s "
                "solver=%s solution_count=%s search_states=%s elapsed_ms=%s "
                "objectives=%s",
                state["thread_id"],
                problem.id,
                result.status.value,
                result.solver,
                len(result.solutions),
                result.search_states,
                result.elapsed_ms,
                objective_summary or "none",
            )
        _log_node_completed(
            state,
            "solve_candidate_variants",
            "optimization_solved",
            solution_count=len(drafts),
            degraded=str(result.status is OptimizationSolveStatus.DEGRADED).lower(),
        )
        return {
            "optimization_result": result,
            "candidate_drafts": drafts,
            "status": "optimization_solved",
        }

    def _materialize(state: TravelState, node_name: str) -> dict:
        _log_node_started(state, node_name)
        candidates = materialize_candidates(
            state["trip"],
            state["candidate_drafts"],
            state["planning_pois"],
            state["route_results"],
            route_strategy=policy.route_strategy,
            route_modes=policy.route_modes,
            max_walking_leg_meters=policy.max_walking_leg_meters,
        )
        _log_candidates(state, candidates)
        _log_node_completed(
            state,
            node_name,
            "candidates_materialized",
            candidate_count=len(candidates),
        )
        return {"candidates": candidates, "status": "candidates_materialized"}

    def materialize_optimized(state: TravelState) -> dict:
        return _materialize(state, "materialize_optimized_candidates")

    def materialize_repaired(state: TravelState) -> dict:
        return _materialize(state, "materialize_candidates")

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
        update: dict[str, object] = {
            "candidates": validated,
            "status": "validated",
        }
        pending_round = state["pending_replan_round"]
        if pending_round is not None:
            report = state["critic_report"]
            plan = state["repair_plan"]
            if report is None or plan is None or len(validated) != 1:
                raise RuntimeError("repair validation requires one candidate and repair context")
            repaired = validated[0]
            day_by_date = {day.date.isoformat(): day for day in repaired.days}
            for day_text, expected_fingerprint in state[
                "preserved_day_hashes"
            ].items():
                day = day_by_date.get(day_text)
                if day is None or day_fingerprint(day) != expected_fingerprint:
                    raise RuntimeError(
                        f"local repair changed preserved day: {day_text}"
                    )

            after_fingerprint = violation_fingerprint(repaired)
            after_errors = error_violations(repaired)
            after_error_types = {violation.type for violation in after_errors}
            resolved = repaired.validation.status in _DELIVERABLE_STATUSES
            repeated_fingerprint = (
                after_fingerprint == report.violation_fingerprint
                or any(
                    after_fingerprint
                    in {
                        attempt.before_violation_fingerprint,
                        attempt.after_violation_fingerprint,
                    }
                    for attempt in state["repair_history"]
                )
            )
            source_resolved = bool(
                set(plan.source_violation_types) - after_error_types
            )
            improved = (
                len(after_errors) < report.error_count
                or (
                    source_resolved
                    and len(after_errors) <= report.error_count
                )
            )
            terminal_reason: str | None = None
            if resolved:
                outcome = RepairOutcome.RESOLVED
            elif repeated_fingerprint:
                outcome = RepairOutcome.NO_PROGRESS
                terminal_reason = "repeated_violation_fingerprint"
            elif improved:
                outcome = RepairOutcome.IMPROVED
            else:
                outcome = RepairOutcome.NO_PROGRESS
                terminal_reason = "repair_no_progress"

            attempt = RepairAttempt(
                round=pending_round,
                target_candidate_id=plan.target_candidate_id,
                before_violation_fingerprint=report.violation_fingerprint,
                after_violation_fingerprint=after_fingerprint,
                before_error_count=report.error_count,
                after_error_count=len(after_errors),
                action_fingerprint=plan.action_fingerprint,
                action_kinds=tuple(action.kind for action in plan.actions),
                outcome=outcome,
                affected_days=plan.affected_days,
                preserved_day_count=len(plan.preserved_days),
                reused_route_count=state["last_route_reused_count"],
                loaded_route_count=state["last_route_loaded_count"],
                terminal_reason=terminal_reason,
            )
            update.update(
                {
                    "iterations": pending_round,
                    "pending_replan_round": None,
                    "repair_history": [*state["repair_history"], attempt],
                    "repair_terminal_reason": terminal_reason,
                }
            )
            logger.info(
                "repair.validation.delta | thread_id=%s round=%s candidate_id=%s "
                "before_errors=%s after_errors=%s outcome=%s affected_days=%s "
                "preserved_days=%s reused_routes=%s loaded_routes=%s",
                state["thread_id"],
                pending_round,
                repaired.id,
                report.error_count,
                len(after_errors),
                outcome.value,
                len(plan.affected_days),
                len(plan.preserved_days),
                state["last_route_reused_count"],
                state["last_route_loaded_count"],
            )
            logger.info(
                "replan.completed | thread_id=%s round=%s status=validated "
                "candidate_count=%s deliverable_count=%s",
                state["thread_id"],
                pending_round,
                len(validated),
                deliverable_count,
            )
            if resolved or terminal_reason is not None:
                logger.info(
                    "repair.terminated | thread_id=%s round=%s outcome=%s reason=%s",
                    state["thread_id"],
                    pending_round,
                    outcome.value,
                    terminal_reason or "resolved",
                )
        return update

    def select_repair_target(state: TravelState) -> dict:
        _log_node_started(state, "select_repair_target")
        target = choose_repair_target(
            state["candidates"], state["trip"], state["planning_pois"]
        )
        logger.info(
            "repair.target.selected | thread_id=%s candidate_id=%s "
            "candidate_count=%s score=%s",
            state["thread_id"],
            target.id,
            len(state["candidates"]),
            target.score,
        )
        _log_node_completed(
            state,
            "select_repair_target",
            "repair_target_selected",
            candidate_id=target.id,
        )
        return {
            "repair_target_candidate_id": target.id,
            "critic_report": None,
            "repair_plan": None,
            "status": "repair_target_selected",
        }

    def analyze_violations(state: TravelState) -> dict:
        _log_node_started(state, "analyze_violations")
        target_id = state["repair_target_candidate_id"]
        candidate = next(
            candidate
            for candidate in state["candidates"]
            if candidate.id == target_id
        )
        report = analyze_candidate(
            candidate, state["trip"], state["planning_pois"]
        )
        logger.info(
            "critic.completed | thread_id=%s candidate_id=%s errors=%s "
            "warnings=%s violation_types=%s affected_days=%s repairable=%s "
            "terminal_reason=%s",
            state["thread_id"],
            candidate.id,
            report.error_count,
            report.warning_count,
            ",".join(report.violation_types) or "none",
            len(report.affected_days),
            str(report.repairable).lower(),
            report.terminal_reason or "none",
        )
        _log_node_completed(
            state,
            "analyze_violations",
            "violations_analyzed",
            repairable=str(report.repairable).lower(),
        )
        return {
            "critic_report": report,
            "repair_terminal_reason": report.terminal_reason,
            "status": "violations_analyzed",
        }

    def create_repair_plan(state: TravelState) -> dict:
        _log_node_started(state, "build_repair_plan")
        target_id = state["repair_target_candidate_id"]
        candidate = next(
            candidate
            for candidate in state["candidates"]
            if candidate.id == target_id
        )
        draft = next(
            draft
            for draft in state["candidate_drafts"]
            if draft.id == target_id
        )
        next_round = state["iterations"] + 1
        report = state["critic_report"]
        if report is None:
            raise RuntimeError("repair plan requires critic report")
        plan, terminal_reason = build_repair_plan(
            state["trip"],
            candidate,
            draft,
            state["planning_pois"],
            report,
            repair_round=next_round,
        )
        if plan is not None and any(
            attempt.action_fingerprint == plan.action_fingerprint
            for attempt in state["repair_history"]
        ):
            terminal_reason = "repeated_repair_action"
            plan = None
        logger.info(
            "repair.plan.created | thread_id=%s round=%s candidate_id=%s "
            "action_count=%s action_types=%s affected_days=%s status=%s reason=%s",
            state["thread_id"],
            next_round,
            candidate.id,
            len(plan.actions) if plan else 0,
            (
                ",".join(action.kind.value for action in plan.actions)
                if plan
                else "none"
            ),
            len(plan.affected_days) if plan else 0,
            "ready" if plan else "unavailable",
            terminal_reason or "none",
        )
        _log_node_completed(
            state,
            "build_repair_plan",
            "repair_plan_ready" if plan else "repair_unavailable",
        )
        return {
            "repair_plan": plan,
            "repair_terminal_reason": terminal_reason,
            "status": "repair_plan_ready" if plan else "repair_unavailable",
        }

    def apply_local_repair(state: TravelState) -> dict:
        _log_node_started(state, "apply_local_repair")
        target_id = state["repair_target_candidate_id"]
        candidate = next(
            candidate
            for candidate in state["candidates"]
            if candidate.id == target_id
        )
        draft = next(
            draft
            for draft in state["candidate_drafts"]
            if draft.id == target_id
        )
        plan = state["repair_plan"]
        if plan is None:
            raise RuntimeError("local repair requires repair plan")
        repaired_draft, applied_plan = apply_repair_plan(
            state["trip"],
            draft,
            state["planning_pois"],
            plan,
            route_strategy=policy.route_strategy,
            route_modes=policy.route_modes,
            max_walking_leg_meters=policy.max_walking_leg_meters,
        )
        preserved_hashes = {
            day.date.isoformat(): day_fingerprint(day)
            for day in candidate.days
            if day.date in applied_plan.preserved_days
        }
        logger.info(
            "repair.action.applied | thread_id=%s round=%s candidate_id=%s "
            "repaired_candidate_id=%s action_count=%s affected_days=%s "
            "preserved_days=%s",
            state["thread_id"],
            applied_plan.round,
            candidate.id,
            repaired_draft.id,
            len(applied_plan.actions),
            len(applied_plan.affected_days),
            len(applied_plan.preserved_days),
        )
        logger.info(
            "repair.routes.invalidated | thread_id=%s round=%s route_count=%s",
            state["thread_id"],
            applied_plan.round,
            len(applied_plan.invalidated_route_keys),
        )
        _log_node_completed(
            state,
            "apply_local_repair",
            "local_repair_applied",
            candidate_id=repaired_draft.id,
        )
        return {
            "pending_replan_round": applied_plan.round,
            "candidate_drafts": [repaired_draft],
            "route_queries": [],
            "delta_route_queries": [],
            "reused_route_keys": [],
            "candidates": [],
            "selected_plan": None,
            "repair_plan": applied_plan,
            "preserved_day_hashes": preserved_hashes,
            "last_route_loaded_count": 0,
            "last_route_reused_count": 0,
            "status": "local_repair_applied",
            "message": (
                f"第 {applied_plan.round} 轮局部修复："
                f"影响 {len(applied_plan.affected_days)} 个日期"
            ),
        }

    def collect_delta_routes(state: TravelState) -> dict:
        _log_node_started(state, "collect_delta_routes")
        draft = state["candidate_drafts"][0]
        delta = collect_route_delta(
            state["trip"],
            draft,
            state["planning_pois"],
            state["route_results"],
            route_strategy=policy.route_strategy,
            route_modes=policy.route_modes,
            max_walking_leg_meters=policy.max_walking_leg_meters,
        )
        logger.info(
            "repair.routes.reused | thread_id=%s round=%s required=%s "
            "reused=%s missing=%s",
            state["thread_id"],
            state["pending_replan_round"],
            len(delta.required_queries),
            len(delta.reused_route_keys),
            len(delta.missing_queries),
        )
        _log_node_completed(
            state,
            "collect_delta_routes",
            "delta_routes_collected",
            reused=len(delta.reused_route_keys),
            missing=len(delta.missing_queries),
        )
        return {
            "route_queries": list(delta.required_queries),
            "delta_route_queries": list(delta.missing_queries),
            "reused_route_keys": list(delta.reused_route_keys),
            "last_route_reused_count": len(delta.reused_route_keys),
            "last_route_loaded_count": 0,
            "status": "delta_routes_collected",
        }

    async def load_delta_routes(state: TravelState) -> dict:
        _log_node_started(state, "load_delta_routes")
        queries: list[RouteQuery] = state["delta_route_queries"]
        results = await gateway.get_routes(
            queries,
            ToolCallContext(thread_id=state["thread_id"]),
        )
        for result in results.values():
            if result.status is ToolStatus.FAILED:
                raise ToolUnavailableError.from_result(result, state["thread_id"])
        loaded = {}
        for key, result in results.items():
            assert result.data is not None
            loaded[key] = result.data
        summaries = [
            _tool_summary(
                result,
                f"route.get_{result.data.mode.value}" if result.data else "route.get",
            )
            for result in results.values()
        ]
        logger.info(
            "repair.routes.loaded | thread_id=%s round=%s query_count=%s "
            "route_count=%s",
            state["thread_id"],
            state["pending_replan_round"],
            len(queries),
            len(loaded),
        )
        _log_node_completed(
            state,
            "load_delta_routes",
            "delta_routes_loaded",
            route_count=len(loaded),
        )
        return {
            "route_results": {**state["route_results"], **loaded},
            "last_route_loaded_count": len(loaded),
            "tool_summaries": [*state["tool_summaries"], *summaries],
            "status": "delta_routes_loaded",
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
            "planning.infeasible | thread_id=%s iterations=%s candidate_count=%s "
            "reason=%s",
            state["thread_id"],
            state["iterations"],
            len(state["candidates"]),
            state["repair_terminal_reason"] or "budget_or_candidates_exhausted",
        )
        _log_node_completed(state, "mark_infeasible", "infeasible")
        return {
            "selected_plan": None,
            "status": "infeasible",
            "message": (
                "在当前约束和局部修复预算内没有找到合法方案"
                f"（{state['repair_terminal_reason']}）"
                if state["repair_terminal_reason"]
                else "在当前约束和局部修复预算内没有找到合法方案"
            ),
        }

    builder = StateGraph(TravelState)
    builder.add_node("build_search_plan", build_search_plan)
    builder.add_node("load_pois", load_pois)
    builder.add_node("resolve_poi_facts", resolve_poi_facts)
    builder.add_node("build_route_matrix", build_route_matrix)
    builder.add_node("build_optimization_problem", build_optimization_problem)
    builder.add_node("solve_candidate_variants", solve_candidate_variants)
    builder.add_node("materialize_optimized_candidates", materialize_optimized)
    builder.add_node("materialize_candidates", materialize_repaired)
    builder.add_node("validate_candidates", validate)
    builder.add_node("select_repair_target", select_repair_target)
    builder.add_node("analyze_violations", analyze_violations)
    builder.add_node("build_repair_plan", create_repair_plan)
    builder.add_node("apply_local_repair", apply_local_repair)
    builder.add_node("collect_delta_routes", collect_delta_routes)
    builder.add_node("load_delta_routes", load_delta_routes)
    builder.add_node("select_best", select_best)
    builder.add_node("mark_infeasible", mark_infeasible)

    builder.add_edge(START, "build_search_plan")
    builder.add_edge("build_search_plan", "load_pois")
    builder.add_edge("load_pois", "resolve_poi_facts")
    builder.add_edge("resolve_poi_facts", "build_route_matrix")
    builder.add_edge("build_route_matrix", "build_optimization_problem")
    builder.add_edge("build_optimization_problem", "solve_candidate_variants")
    builder.add_edge(
        "solve_candidate_variants", "materialize_optimized_candidates"
    )
    builder.add_edge("materialize_optimized_candidates", "validate_candidates")
    builder.add_edge("materialize_candidates", "validate_candidates")
    builder.add_conditional_edges("validate_candidates", route_after_validation)
    builder.add_edge("select_repair_target", "analyze_violations")
    builder.add_conditional_edges("analyze_violations", route_after_critic)
    builder.add_conditional_edges("build_repair_plan", route_after_repair_plan)
    builder.add_edge("apply_local_repair", "collect_delta_routes")
    builder.add_conditional_edges(
        "collect_delta_routes", route_after_delta_routes
    )
    builder.add_edge("load_delta_routes", "materialize_candidates")
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
        "optimization_pois": [],
        "optimization_problem": None,
        "optimization_result": None,
        "route_matrix_cache_hits": 0,
        "route_matrix_provider_calls": 0,
        "poi_resolution_issues": [],
        "candidate_drafts": [],
        "route_queries": [],
        "delta_route_queries": [],
        "route_results": {},
        "reused_route_keys": [],
        "tool_summaries": [],
        "candidates": [],
        "selected_plan": None,
        "iterations": 0,
        "pending_replan_round": None,
        "max_replan_rounds": request.max_replan_rounds,
        "repair_target_candidate_id": None,
        "critic_report": None,
        "repair_plan": None,
        "repair_history": [],
        "preserved_day_hashes": {},
        "repair_terminal_reason": None,
        "last_route_loaded_count": 0,
        "last_route_reused_count": 0,
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
        recursion_limit = max(16, 12 + request.max_replan_rounds * 8)
        result = await workflow.ainvoke(
            initial_state(request, run_thread_id),
            config={
                "configurable": {"thread_id": run_thread_id},
                "recursion_limit": recursion_limit,
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
