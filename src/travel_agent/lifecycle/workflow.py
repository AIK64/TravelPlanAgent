from __future__ import annotations

from datetime import date
from hashlib import sha256
import logging
import secrets
from typing import Literal, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt

from travel_agent.critique.errors import CriticUnavailableError
from travel_agent.critique.evidence import EvidenceBudget, build_evidence_digests
from travel_agent.critique.gateway import CriticGateway
from travel_agent.critique.grounding import validate_critique_grounding
from travel_agent.critique.quality import CriticPolicy, quality_score
from travel_agent.domain.critique_models import CriticStatus, SoftCriticRequest
from travel_agent.domain.lifecycle_models import (
    ActionReceipt,
    ApprovalAction,
    ChangeSource,
    EditModelInput,
    EditClarificationAction,
    EditPatch,
    ImpactScope,
    LifecycleInterrupt,
    LifecycleResumeRequest,
    LockAction,
    LockKind,
    PlanLock,
    PlanChangeTrigger,
    PlanPreview,
    PlanSessionRecord,
    PlanSessionResponse,
    PlanSessionStatus,
    PlanVersion,
    PreviewStatus,
    SelectCandidateAction,
    StructuredEditAction,
    TextEditAction,
)
from travel_agent.domain.models import PlanningPOI, ValidationStatus, ViolationSeverity
from travel_agent.domain.tool_models import ToolCallContext, ToolStatus, route_key
from travel_agent.domain.tool_models import POISearchQuery
from travel_agent.edits.gateway import EditGateway
from travel_agent.execution.checkpoints import ObservedCheckpointSaver
from travel_agent.execution.instrumentation import (
    execution_budget_guard,
    instrument_node,
    instrument_route,
)
from travel_agent.execution.context import record_degradation
from travel_agent.execution.errors import ExecutionBudgetExceeded
from travel_agent.lifecycle.actions import (
    apply_edit_patch,
    edit_item_context,
    ground_edit_patch,
)
from travel_agent.lifecycle.diff import build_plan_diff
from travel_agent.lifecycle.errors import (
    LifecycleActionError,
    LifecycleConflictError,
    LifecycleNotFoundError,
)
from travel_agent.lifecycle.fingerprints import (
    day_fingerprint,
    item_fingerprint,
    plan_fingerprint,
    with_stable_item_ids,
)
from travel_agent.lifecycle.impact import analyze_change_impact
from travel_agent.lifecycle.repository import PlanRepository
from travel_agent.lifecycle.state import PlanLifecycleState
from travel_agent.planning.impact import collect_route_delta, invalidated_route_keys
from travel_agent.planning.defaults import POIDefaultPolicy
from travel_agent.planning.planner import materialize_candidates
from travel_agent.planning.policy import PlanningPolicy
from travel_agent.planning.validator import validate_candidate
from travel_agent.tools.errors import ToolUnavailableError
from travel_agent.tools.gateway import ToolGateway
from travel_agent.domain.weather_models import (
    ChangeEvent,
    ChangeEventKind,
    DailyWeatherRisk,
    DismissWeatherEventAction,
    RefreshWeatherAction,
    WeatherEventReceipt,
    WeatherEventStatus,
    WeatherImpactResult,
    WeatherRefreshOutcome,
    WeatherRepairPlan,
    WeatherSnapshot,
)
from travel_agent.weather.errors import WeatherUnavailableError
from travel_agent.weather.events import build_weather_snapshot, derive_change_event
from travel_agent.weather.gateway import WeatherToolGateway
from travel_agent.weather.impact import analyze_weather_impact
from travel_agent.weather.persistence import (
    find_event_by_fingerprint,
    mark_weather_failed,
    persist_weather_observation,
    utcnow as weather_utcnow,
    weather_state_view,
)
from travel_agent.weather.policy import WEATHER_POLICY_VERSION, classify_forecast
from travel_agent.weather.repair import (
    build_weather_repair_plan,
    repair_plan_to_edit_patch,
)
from travel_agent.weather.workflow import resolve_indoor_alternatives


logger = logging.getLogger(__name__)


def _log(state: PlanLifecycleState, event: str, **fields: object) -> None:
    details = " ".join(f"{key}={value}" for key, value in fields.items())
    logger.info(
        "%s | session_id=%s lifecycle_thread_id=%s status=%s%s",
        event,
        state["session_id"],
        state["lifecycle_thread_id"],
        state["status"],
        f" {details}" if details else "",
    )


def _request(state: PlanLifecycleState) -> LifecycleResumeRequest:
    if state["action"] is None:
        raise RuntimeError("lifecycle action is missing")
    return LifecycleResumeRequest.model_validate(state["action"])


def _ensure_expected(session: PlanSessionRecord, request: LifecycleResumeRequest) -> None:
    if (
        request.expected_session_revision is not None
        and request.expected_session_revision != session.session_revision
    ):
        raise LifecycleConflictError(session.session_id, code="stale_revision")
    if (
        request.expected_active_version_id is not None
        and request.expected_active_version_id != session.active_version_id
    ):
        raise LifecycleConflictError(session.session_id, code="stale_version")


def _active_version(session: PlanSessionRecord) -> PlanVersion:
    if session.active_version_id is None:
        raise LifecycleActionError(
            session.session_id, "plan_not_selected", "请先选择候选计划"
        )
    return session.versions[session.active_version_id]


def _receipt(
    request: LifecycleResumeRequest,
    revision: int,
    *,
    version_id: str | None = None,
    preview_id: str | None = None,
    event_id: str | None = None,
) -> ActionReceipt:
    return ActionReceipt(
        request_id=str(request.request_id),
        action_kind=request.action.kind,
        resulting_revision=revision,
        resulting_version_id=version_id,
        resulting_preview_id=preview_id,
        resulting_event_id=event_id,
    )


def _approval_hash(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def build_lifecycle_workflow(
    *,
    repository: PlanRepository,
    tool_gateway: ToolGateway,
    edit_gateway: EditGateway,
    planning_policy: PlanningPolicy,
    poi_default_policy: POIDefaultPolicy,
    critic_gateway: CriticGateway | None = None,
    critic_policy: CriticPolicy = CriticPolicy(),
    evidence_budget: EvidenceBudget = EvidenceBudget(max_candidates=1),
    max_affected_days: int = 2,
    max_versions: int = 20,
    weather_gateway: WeatherToolGateway | None = None,
    weather_max_events: int = 50,
    weather_max_poi_searches: int = 4,
    weather_max_alternatives: int = 6,
    weather_exposure_min_confidence: float = 0.8,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    async def await_user_action(state: PlanLifecycleState) -> dict:
        session = await repository.get(state["session_id"])
        if session.status is PlanSessionStatus.AWAITING_CANDIDATE_SELECTION:
            assert session.snapshot is not None
            payload = {
                "kind": "candidate_selection",
                "recommended_candidate_id": session.snapshot.recommended_candidate_id,
                "candidate_ids": [item.id for item in session.snapshot.candidates],
                "session_revision": session.session_revision,
            }
        elif session.status is PlanSessionStatus.AWAITING_CHANGE_APPROVAL:
            preview = session.previews[cast(str, session.pending_preview_id)]
            payload = {
                "kind": "change_approval",
                "preview_id": preview.preview_id,
                "base_version_id": preview.base_version_id,
                "session_revision": session.session_revision,
                "approval_token": state["approval_token"],
                "affected_dates": [day.isoformat() for day in preview.impact.affected_dates],
            }
            if preview.change_trigger is not None:
                payload["change_source"] = preview.change_trigger.source.value
                payload["weather_event_id"] = preview.change_trigger.event_id
        elif session.status is PlanSessionStatus.NEEDS_EDIT_CLARIFICATION:
            version = _active_version(session)
            payload = {
                "kind": "edit_clarification",
                "question": "请从候选项目中选择要修改的 item_id",
                "round": state["clarification_round"],
                "items": list(edit_item_context(version.candidate)),
                "session_revision": session.session_revision,
            }
        else:
            payload = {
                "kind": "plan_change",
                "active_version_id": session.active_version_id,
                "session_revision": session.session_revision,
                "allowed_actions": list(_allowed_actions(session)),
            }
            if session.weather_monitor.attention_event_id is not None:
                payload["weather_attention_event_id"] = (
                    session.weather_monitor.attention_event_id
                )
        _log(state, "lifecycle.interrupted", kind=payload["kind"])
        value = interrupt(payload)
        return {
            "resume_value": value,
            "status": session.status.value,
            "transition_count": state["transition_count"] + 1,
        }

    def dispatch_action(state: PlanLifecycleState) -> dict:
        resume_value = state["resume_value"]
        if not isinstance(resume_value, dict):
            raise ValueError("lifecycle resume value must be an object")
        request = LifecycleResumeRequest.model_validate(resume_value)
        _log(state, "lifecycle.resumed", action=request.action.kind, request_id=request.request_id)
        return {
            "action": request.model_dump(mode="json"),
            "resume_value": None,
            "approval_token": None,
            "message": None,
            "weather_location": None,
            "weather_snapshot": None,
            "weather_risks": None,
            "weather_event": None,
            "weather_impact": None,
            "weather_repair_plan": None,
            "weather_alternatives": None,
            "weather_decision": None,
        }

    def route_action(
        state: PlanLifecycleState,
    ) -> Literal[
        "select_candidate",
        "change_lock",
        "parse_edit",
        "apply_edit_clarification",
        "resolve_approval",
        "resolve_weather_location",
        "dismiss_weather_event",
    ]:
        kind = _request(state).action.kind
        if kind in {"accept_recommendation", "select_candidate"}:
            return "select_candidate"
        if kind in {"lock", "unlock"}:
            return "change_lock"
        if kind in {"edit", "edit_text"}:
            return "parse_edit"
        if kind == "clarify_edit":
            return "apply_edit_clarification"
        if kind in {"approve_preview", "reject_preview"}:
            return "resolve_approval"
        if kind == "refresh_weather":
            return "resolve_weather_location"
        if kind == "dismiss_weather_event":
            return "dismiss_weather_event"
        raise LifecycleActionError(state["session_id"], "invalid_action", "当前阶段不支持该动作")

    async def persist_weather_terminal(
        state: PlanLifecycleState,
        *,
        session: PlanSessionRecord,
        request: LifecycleResumeRequest,
        snapshot: WeatherSnapshot,
        risks: tuple[DailyWeatherRisk, ...],
        outcome: WeatherRefreshOutcome,
        event: ChangeEvent | None,
        event_status: WeatherEventStatus | None,
        message: str,
    ) -> dict:
        previous = session.session_revision
        persist_weather_observation(
            session,
            snapshot=snapshot,
            risks=risks,
            outcome=outcome,
            event=event,
            event_status=event_status,
            max_events=weather_max_events,
        )
        session.status = PlanSessionStatus.ACTIVE
        session.session_revision += 1
        session.receipts[str(request.request_id)] = _receipt(
            request,
            session.session_revision,
            event_id=event.event_id if event is not None else None,
        )
        await repository.save(session, expected_revision=previous)
        _log(
            state,
            "weather.refresh.completed",
            outcome=outcome.value,
            event_id=event.event_id if event is not None else None,
        )
        return {
            "status": session.status.value,
            "action": None,
            "weather_decision": "terminal",
            "message": message,
        }

    async def persist_weather_failure(
        session: PlanSessionRecord,
        result,
    ) -> None:
        previous = session.session_revision
        error = result.error
        assert error is not None
        mark_weather_failed(session, safe_error_code=error.code)
        session.session_revision += 1
        await repository.save(session, expected_revision=previous)

    async def resolve_weather_location(state: PlanLifecycleState) -> dict:
        request = _request(state)
        session = await repository.get(state["session_id"])
        _ensure_expected(session, request)
        if weather_gateway is None:
            raise LifecycleActionError(
                session.session_id,
                "weather_not_configured",
                "天气工具尚未配置",
            )
        if session.status not in {
            PlanSessionStatus.ACTIVE,
            PlanSessionStatus.CHANGE_REJECTED,
            PlanSessionStatus.REQUIRES_NEW_PLAN,
        }:
            code = (
                "pending_preview_exists"
                if session.status is PlanSessionStatus.AWAITING_CHANGE_APPROVAL
                else "invalid_lifecycle_state"
            )
            raise LifecycleConflictError(session.session_id, code=code)
        version = _active_version(session)
        assert version is not None and session.snapshot is not None
        location = session.weather_monitor.location
        if location is None:
            result = await weather_gateway.resolve_location(
                session.snapshot.trip.destination,
                ToolCallContext(thread_id=session.lifecycle_thread_id),
            )
            if result.status is ToolStatus.FAILED:
                await persist_weather_failure(session, result)
                assert result.error is not None
                if result.error.code == "weather_location_unresolved":
                    raise LifecycleActionError(
                        session.session_id,
                        "weather_location_unresolved",
                        result.error.safe_message,
                    )
                raise WeatherUnavailableError.from_result(
                    result, session_id=session.session_id
                )
            assert result.data is not None
            location = result.data
        _log(
            state,
            "weather.location.resolved",
            provider=location.provider,
            adcode=location.adcode,
        )
        return {
            "weather_location": location.model_dump(mode="json"),
            "weather_decision": None,
        }

    async def fetch_weather_snapshot(state: PlanLifecycleState) -> dict:
        request = _request(state)
        session = await repository.get(state["session_id"])
        _ensure_expected(session, request)
        assert weather_gateway is not None and session.snapshot is not None
        from travel_agent.domain.weather_models import WeatherLocation

        location = WeatherLocation.model_validate(state["weather_location"])
        result = await weather_gateway.get_forecast(
            location,
            start_date=session.snapshot.trip.start_date,
            end_date=session.snapshot.trip.end_date,
            context=ToolCallContext(thread_id=session.lifecycle_thread_id),
        )
        if result.status is ToolStatus.FAILED:
            await persist_weather_failure(session, result)
            raise WeatherUnavailableError.from_result(
                result, session_id=session.session_id
            )
        assert result.data is not None
        fetched_at = result.fetched_at or weather_utcnow()
        expires_at = result.expires_at or fetched_at
        snapshot = build_weather_snapshot(
            result.data,
            fetched_at=fetched_at,
            expires_at=expires_at,
        )
        _log(
            state,
            "weather.snapshot.fetched",
            snapshot_id=snapshot.snapshot_id,
            covered_days=len(snapshot.days),
            cache_hit=result.cache_hit,
        )
        return {"weather_snapshot": snapshot.model_dump(mode="json")}

    def classify_weather_risks(state: PlanLifecycleState) -> dict:
        snapshot = WeatherSnapshot.model_validate(state["weather_snapshot"])
        risks = classify_forecast(snapshot.days)
        severe = sum(item.level.value == "severe" for item in risks)
        warning = sum(item.level.value == "warning" for item in risks)
        _log(
            state,
            "weather.risk.classified",
            policy_version=WEATHER_POLICY_VERSION,
            severe_days=severe,
            warning_days=warning,
        )
        return {
            "weather_risks": [item.model_dump(mode="json") for item in risks]
        }

    async def derive_weather_change(state: PlanLifecycleState) -> dict:
        session = await repository.get(state["session_id"])
        assert session.snapshot is not None
        version = _active_version(session)
        snapshot = WeatherSnapshot.model_validate(state["weather_snapshot"])
        risks = tuple(
            DailyWeatherRisk.model_validate(item)
            for item in state["weather_risks"] or []
        )
        previous_snapshot = (
            session.weather_snapshots.get(session.weather_monitor.latest_snapshot_id)
            if session.weather_monitor.latest_snapshot_id
            else None
        )
        previous_risks = (
            session.weather_risks.get(previous_snapshot.snapshot_id, ())
            if previous_snapshot is not None
            else ()
        )
        trip_dates = tuple(day.date for day in version.candidate.days)
        event = derive_change_event(
            session_id=session.session_id,
            base_version_id=version.version_id,
            current_snapshot=snapshot,
            current_risks=risks,
            previous_snapshot=previous_snapshot,
            previous_risks=previous_risks,
            trip_dates=trip_dates,
        )
        _log(
            state,
            "weather.event.derived",
            event_id=event.event_id if event is not None else None,
            kind=event.kind.value if event is not None else "none",
        )
        return {
            "weather_event": (
                event.model_dump(mode="json") if event is not None else None
            )
        }

    async def deduplicate_weather_event(state: PlanLifecycleState) -> dict:
        request = _request(state)
        session = await repository.get(state["session_id"])
        _ensure_expected(session, request)
        snapshot = WeatherSnapshot.model_validate(state["weather_snapshot"])
        risks = tuple(
            DailyWeatherRisk.model_validate(item)
            for item in state["weather_risks"] or []
        )
        event = (
            ChangeEvent.model_validate(state["weather_event"])
            if state["weather_event"] is not None
            else None
        )
        if event is None:
            return await persist_weather_terminal(
                state,
                session=session,
                request=request,
                snapshot=snapshot,
                risks=risks,
                outcome=WeatherRefreshOutcome.NO_CHANGE,
                event=None,
                event_status=None,
                message="天气事实已刷新，未发现需要调整计划的变化",
            )
        existing = find_event_by_fingerprint(session, event.event_fingerprint)
        if existing is not None:
            return await persist_weather_terminal(
                state,
                session=session,
                request=request,
                snapshot=snapshot,
                risks=risks,
                outcome=WeatherRefreshOutcome.DUPLICATE,
                event=existing,
                event_status=None,
                message="相同天气事件已经处理，本次未重复创建 Preview",
            )
        if event.kind is ChangeEventKind.WEATHER_RECOVERED:
            return await persist_weather_terminal(
                state,
                session=session,
                request=request,
                snapshot=snapshot,
                risks=risks,
                outcome=WeatherRefreshOutcome.RECOVERED,
                event=event,
                event_status=WeatherEventStatus.NO_PLAN_IMPACT,
                message="天气风险已经恢复；当前版本不会被自动回滚",
            )
        return {"weather_decision": "analyze"}

    def route_weather_event(
        state: PlanLifecycleState,
    ) -> Literal["analyze_weather_impact", "await_user_action"]:
        return (
            "analyze_weather_impact"
            if state["weather_decision"] == "analyze"
            else "await_user_action"
        )

    async def analyze_weather_change_impact(state: PlanLifecycleState) -> dict:
        request = _request(state)
        session = await repository.get(state["session_id"])
        version = _active_version(session)
        snapshot = WeatherSnapshot.model_validate(state["weather_snapshot"])
        risks = tuple(
            DailyWeatherRisk.model_validate(item)
            for item in state["weather_risks"] or []
        )
        event = ChangeEvent.model_validate(state["weather_event"])
        impact = analyze_weather_impact(
            event=event,
            candidate=version.candidate,
            planning_pois=version.planning_pois,
            risks=risks,
            locks=tuple(session.locks.values()),
            max_affected_days=max_affected_days,
        )
        _log(
            state,
            "weather.impact.analyzed",
            affected_days=len(impact.affected_dates),
            affected_items=len(impact.affected_item_ids),
            lock_conflicts=len(impact.lock_conflicts),
            attention=impact.requires_user_attention,
        )
        if not impact.affected_item_ids and not impact.requires_user_attention:
            return await persist_weather_terminal(
                state,
                session=session,
                request=request,
                snapshot=snapshot,
                risks=risks,
                outcome=WeatherRefreshOutcome.NO_PLAN_IMPACT,
                event=event,
                event_status=WeatherEventStatus.NO_PLAN_IMPACT,
                message="天气发生变化，但当前计划没有需要调整的户外活动",
            )
        if impact.requires_user_attention:
            return await persist_weather_terminal(
                state,
                session=session,
                request=request,
                snapshot=snapshot,
                risks=risks,
                outcome=WeatherRefreshOutcome.NEEDS_USER_ATTENTION,
                event=event,
                event_status=WeatherEventStatus.NEEDS_USER_ATTENTION,
                message="天气影响涉及锁定或未知活动，请先确认后再修改计划",
            )
        return {
            "weather_impact": impact.model_dump(mode="json"),
            "weather_decision": "repair",
        }

    async def build_weather_repair(state: PlanLifecycleState) -> dict:
        request = _request(state)
        session = await repository.get(state["session_id"])
        _ensure_expected(session, request)
        assert session.snapshot is not None
        version = _active_version(session)
        snapshot = WeatherSnapshot.model_validate(state["weather_snapshot"])
        risks = tuple(
            DailyWeatherRisk.model_validate(item)
            for item in state["weather_risks"] or []
        )
        event = ChangeEvent.model_validate(state["weather_event"])
        impact = WeatherImpactResult.model_validate(state["weather_impact"])
        alternatives = await resolve_indoor_alternatives(
            session_id=session.session_id,
            lifecycle_thread_id=session.lifecycle_thread_id,
            trip=session.snapshot.trip,
            candidate=version.candidate,
            planning_pois=version.planning_pois,
            tool_gateway=tool_gateway,
            default_policy=poi_default_policy,
            planning_policy=planning_policy,
            max_searches=weather_max_poi_searches,
            max_alternatives=weather_max_alternatives,
            minimum_confidence=weather_exposure_min_confidence,
        )
        repair = build_weather_repair_plan(
            event_id=event.event_id,
            base_version_id=version.version_id,
            trip=session.snapshot.trip,
            candidate=version.candidate,
            impact=impact,
            risks=risks,
            alternatives=alternatives,
            locks=tuple(session.locks.values()),
        )
        if repair is None:
            return await persist_weather_terminal(
                state,
                session=session,
                request=request,
                snapshot=snapshot,
                risks=risks,
                outcome=WeatherRefreshOutcome.NEEDS_USER_ATTENTION,
                event=event,
                event_status=WeatherEventStatus.NEEDS_USER_ATTENTION,
                message="没有找到满足硬约束的安全局部替代方案",
            )
        patch = repair_plan_to_edit_patch(repair)
        _log(
            state,
            "weather.repair_plan.built",
            event_id=event.event_id,
            action_count=len(repair.actions),
        )
        return {
            "weather_repair_plan": repair.model_dump(mode="json"),
            "weather_alternatives": [
                item.model_dump(mode="json") for item in alternatives
            ],
            "edit_patch": patch.model_dump(mode="json"),
            "weather_decision": "local_replan",
        }

    def route_weather_impact(
        state: PlanLifecycleState,
    ) -> Literal["build_weather_repair_plan", "await_user_action"]:
        return (
            "build_weather_repair_plan"
            if state["weather_decision"] == "repair"
            else "await_user_action"
        )

    def route_weather_repair(
        state: PlanLifecycleState,
    ) -> Literal["analyze_change_impact", "await_user_action"]:
        return (
            "analyze_change_impact"
            if state["weather_decision"] == "local_replan"
            else "await_user_action"
        )

    async def dismiss_weather_event(state: PlanLifecycleState) -> dict:
        request = _request(state)
        action = cast(DismissWeatherEventAction, request.action)
        session = await repository.get(state["session_id"])
        _ensure_expected(session, request)
        event = session.weather_events.get(action.event_id)
        receipt = session.weather_monitor.event_receipts.get(action.event_id)
        if event is None or receipt is None:
            raise LifecycleActionError(
                session.session_id,
                "weather_event_not_found",
                "天气事件不存在",
            )
        if receipt.status not in {
            WeatherEventStatus.NEEDS_USER_ATTENTION,
            WeatherEventStatus.OBSERVED,
        }:
            raise LifecycleConflictError(
                session.session_id, code="weather_event_already_resolved"
            )
        previous = session.session_revision
        receipt.status = WeatherEventStatus.DISMISSED
        session.weather_monitor.attention_event_id = None
        session.status = PlanSessionStatus.ACTIVE
        session.session_revision += 1
        session.receipts[str(request.request_id)] = _receipt(
            request, session.session_revision, event_id=event.event_id
        )
        await repository.save(session, expected_revision=previous)
        _log(state, "weather.event.dismissed", event_id=event.event_id)
        return {
            "status": session.status.value,
            "action": None,
            "message": "已记录用户保留当前计划的决定",
        }

    async def select_candidate(state: PlanLifecycleState) -> dict:
        request = _request(state)
        session = await repository.get(state["session_id"])
        _ensure_expected(session, request)
        if session.status is not PlanSessionStatus.AWAITING_CANDIDATE_SELECTION:
            raise LifecycleConflictError(session.session_id)
        assert session.snapshot is not None
        candidate_id = (
            session.snapshot.recommended_candidate_id
            if request.action.kind == "accept_recommendation"
            else cast(SelectCandidateAction, request.action).candidate_id
        )
        candidate = next(
            (item for item in session.snapshot.candidates if item.id == candidate_id),
            None,
        )
        if candidate is None:
            raise LifecycleActionError(session.session_id, "unknown_candidate", "候选计划不存在")
        if candidate.validation is None or candidate.validation.status is ValidationStatus.INVALID:
            raise LifecycleActionError(session.session_id, "invalid_candidate", "不能选择硬约束非法候选")
        draft = next(
            (item for item in session.snapshot.candidate_drafts if item.id == candidate_id),
            None,
        )
        if draft is None:
            raise LifecycleActionError(session.session_id, "missing_candidate_draft", "候选缺少可编辑 Draft")
        versioned = with_stable_item_ids(session.session_id, candidate)
        version = PlanVersion(
            version_id="V1",
            number=1,
            source_request_id=str(request.request_id),
            selected_candidate_id=candidate_id,
            candidate=versioned,
            candidate_draft=draft,
            planning_pois=session.snapshot.planning_pois,
            route_results=session.snapshot.route_results,
            plan_fingerprint=plan_fingerprint(versioned),
            critic_status=session.snapshot.critic_status,
        )
        previous = session.session_revision
        session.versions[version.version_id] = version
        session.active_version_id = version.version_id
        session.status = PlanSessionStatus.ACTIVE
        session.session_revision += 1
        session.receipts[str(request.request_id)] = _receipt(
            request, session.session_revision, version_id=version.version_id
        )
        await repository.save(session, expected_revision=previous)
        _log(state, "selection.committed", candidate_id=candidate_id, version_id="V1")
        return {"status": session.status.value, "action": None}

    async def change_lock(state: PlanLifecycleState) -> dict:
        request = _request(state)
        action = cast(LockAction, request.action)
        session = await repository.get(state["session_id"])
        _ensure_expected(session, request)
        if session.status not in {
            PlanSessionStatus.ACTIVE,
            PlanSessionStatus.CHANGE_REJECTED,
            PlanSessionStatus.REQUIRES_NEW_PLAN,
        }:
            raise LifecycleConflictError(session.session_id)
        version = _active_version(session)
        lock_id = f"{action.lock_kind.value}:{action.target_id}"
        if action.kind == "lock":
            if action.lock_kind is LockKind.DAY:
                day = next(
                    (item for item in version.candidate.days if item.date.isoformat() == action.target_id),
                    None,
                )
                if day is None:
                    raise LifecycleActionError(session.session_id, "unknown_day", "锁定日期不存在")
                fingerprint = day_fingerprint(day)
            else:
                located = next(
                    (
                        (day.date, item)
                        for day in version.candidate.days
                        for item in day.items
                        if item.item_id == action.target_id
                    ),
                    None,
                )
                if located is None:
                    raise LifecycleActionError(session.session_id, "unknown_item", "锁定项目不存在")
                fingerprint = item_fingerprint(located[1], located[0])
            session.locks[lock_id] = PlanLock(
                lock_id=lock_id,
                kind=action.lock_kind,
                target_id=action.target_id,
                expected_fingerprint=fingerprint,
                created_by_request_id=str(request.request_id),
            )
        else:
            if lock_id not in session.locks:
                raise LifecycleActionError(session.session_id, "lock_not_found", "要解除的锁不存在")
            del session.locks[lock_id]
        previous = session.session_revision
        session.session_revision += 1
        session.status = PlanSessionStatus.ACTIVE
        session.receipts[str(request.request_id)] = _receipt(request, session.session_revision)
        await repository.save(session, expected_revision=previous)
        _log(state, "lock.changed", action=action.kind, lock_id=lock_id)
        return {"status": session.status.value, "action": None}

    async def parse_edit(state: PlanLifecycleState) -> dict:
        request = _request(state)
        session = await repository.get(state["session_id"])
        _ensure_expected(session, request)
        if session.status not in {
            PlanSessionStatus.ACTIVE,
            PlanSessionStatus.CHANGE_REJECTED,
            PlanSessionStatus.REQUIRES_NEW_PLAN,
        }:
            raise LifecycleConflictError(session.session_id)
        version = _active_version(session)
        if isinstance(request.action, StructuredEditAction):
            patch = request.action.patch
            summary = None
        else:
            action = cast(TextEditAction, request.action)
            context = EditModelInput(
                text=action.text,
                trip_start=session.snapshot.trip.start_date,  # type: ignore[union-attr]
                trip_end=session.snapshot.trip.end_date,  # type: ignore[union-attr]
                items=tuple(edit_item_context(version.candidate)),
            )
            patch, execution = await edit_gateway.parse(context, session_id=session.session_id)
            summary = execution.model_dump(mode="json")
        return {"edit_patch": patch.model_dump(mode="json"), "edit_summary": summary}

    async def apply_edit_clarification(state: PlanLifecycleState) -> dict:
        request = _request(state)
        action = cast(EditClarificationAction, request.action)
        session = await repository.get(state["session_id"])
        _ensure_expected(session, request)
        if session.status is not PlanSessionStatus.NEEDS_EDIT_CLARIFICATION:
            raise LifecycleConflictError(session.session_id)
        version = _active_version(session)
        known_ids = {
            item.item_id
            for day in version.candidate.days
            for item in day.items
            if item.item_id is not None
        }
        if action.item_id not in known_ids:
            raise LifecycleActionError(
                session.session_id, "unknown_item", "澄清选择的 item_id 不存在"
            )
        patch = EditPatch.model_validate(state["edit_patch"])
        operations = list(patch.operations)
        target_index = next(
            (index for index, operation in enumerate(operations) if operation.item_id is None),
            None,
        )
        if target_index is None:
            raise LifecycleActionError(
                session.session_id, "clarification_not_needed", "当前编辑不需要项目澄清"
            )
        operations[target_index] = operations[target_index].model_copy(
            update={"item_id": action.item_id}
        )
        return {
            "edit_patch": EditPatch(operations=tuple(operations)).model_dump(mode="json"),
            "status": PlanSessionStatus.BUILDING_PREVIEW.value,
        }

    async def analyze_impact(state: PlanLifecycleState) -> dict:
        request = _request(state)
        session = await repository.get(state["session_id"])
        version = _active_version(session)
        try:
            patch = ground_edit_patch(
                session.session_id,
                version.candidate,
                EditPatch.model_validate(state["edit_patch"]),
            )
        except LifecycleActionError as error:
            if error.code not in {"ambiguous_item", "unknown_item"}:
                raise
            if state["clarification_round"] >= 2:
                raise LifecycleActionError(
                    session.session_id,
                    "edit_clarification_exhausted",
                    "编辑项目经过两轮仍无法唯一确定",
                ) from error
            previous = session.session_revision
            session.status = PlanSessionStatus.NEEDS_EDIT_CLARIFICATION
            session.session_revision += 1
            session.receipts[str(request.request_id)] = _receipt(
                request, session.session_revision
            )
            await repository.save(session, expected_revision=previous)
            return {
                "status": session.status.value,
                "clarification_round": state["clarification_round"] + 1,
                "message": error.message,
            }
        impact = analyze_change_impact(
            version.candidate,
            patch,
            tuple(session.locks.values()),
            max_affected_days=max_affected_days,
        )
        logger.info(
            "impact.analyzed | session_id=%s request_id=%s scope=%s affected_days=%s "
            "affected_items=%s lock_conflicts=%s",
            session.session_id,
            request.request_id,
            impact.scope.value,
            len(impact.affected_dates),
            len(impact.affected_item_ids),
            len(impact.lock_conflicts),
        )
        if impact.lock_conflicts:
            logger.warning(
                "lock_guard.rejected | session_id=%s conflict_count=%s",
                session.session_id,
                len(impact.lock_conflicts),
            )
            raise LifecycleActionError(session.session_id, "lock_conflict", "编辑会修改已锁定范围")
        if impact.scope is ImpactScope.REQUIRES_NEW_PLAN:
            previous = session.session_revision
            session.status = PlanSessionStatus.REQUIRES_NEW_PLAN
            session.session_revision += 1
            session.receipts[str(request.request_id)] = _receipt(request, session.session_revision)
            await repository.save(session, expected_revision=previous)
            return {
                "edit_patch": patch.model_dump(mode="json"),
                "impact_result": impact.model_dump(mode="json"),
                "status": session.status.value,
                "message": "修改范围超过局部重规划预算，请创建新计划",
            }
        return {
            "edit_patch": patch.model_dump(mode="json"),
            "impact_result": impact.model_dump(mode="json"),
            "status": PlanSessionStatus.BUILDING_PREVIEW.value,
        }

    def route_impact(state: PlanLifecycleState) -> Literal["build_preview", "await_user_action"]:
        return (
            "await_user_action"
            if state["status"]
            in {
                PlanSessionStatus.REQUIRES_NEW_PLAN.value,
                PlanSessionStatus.NEEDS_EDIT_CLARIFICATION.value,
            }
            else "build_preview"
        )

    async def build_preview(state: PlanLifecycleState) -> dict:
        request = _request(state)
        session = await repository.get(state["session_id"])
        _ensure_expected(session, request)
        assert session.snapshot is not None
        version = _active_version(session)
        patch = EditPatch.model_validate(state["edit_patch"])
        planning_pois = list(version.planning_pois)
        for value in state["weather_alternatives"] or []:
            alternative = PlanningPOI.model_validate(value)
            if alternative.facts.id not in {item.facts.id for item in planning_pois}:
                planning_pois.append(alternative)
        existing_names = {
            poi.facts.name.strip().casefold() for poi in planning_pois
        }
        edit_queries = []
        for operation in patch.operations:
            if operation.kind.value not in {"add_item", "replace_item"} or not operation.poi_name:
                continue
            needle = operation.poi_name.strip().casefold()
            if not any(
                needle in name or name in needle for name in existing_names
            ):
                edit_queries.append(
                    POISearchQuery(
                        city=session.snapshot.trip.destination,
                        keyword=operation.poi_name,
                        exact_match=False,
                        limit=min(5, planning_policy.poi_query_limit),
                        priority=100,
                    )
                )
        if edit_queries:
            results = await tool_gateway.search_pois(
                edit_queries,
                ToolCallContext(thread_id=session.lifecycle_thread_id),
            )
            for result in results:
                if result.status is ToolStatus.FAILED:
                    raise ToolUnavailableError.from_result(
                        result, session.lifecycle_thread_id
                    )
                for facts in result.data or []:
                    if facts.id in {item.facts.id for item in planning_pois}:
                        continue
                    resolution = poi_default_policy.resolve(
                        facts, session.snapshot.trip
                    )
                    if resolution.poi is not None:
                        planning_pois.append(resolution.poi)
        impact = analyze_change_impact(
            version.candidate,
            patch,
            tuple(session.locks.values()),
            max_affected_days=max_affected_days,
        )
        edited_draft, item_ids_by_poi = apply_edit_patch(
            session_id=session.session_id,
            request_id=str(request.request_id),
            trip=session.snapshot.trip,
            candidate=version.candidate,
            draft=version.candidate_draft,
            planning_pois=tuple(planning_pois),
            patch=patch,
        )
        invalidated = invalidated_route_keys(
            session.snapshot.trip,
            version.candidate_draft,
            edited_draft,
            planning_pois,
            route_strategy=planning_policy.route_strategy,
            route_modes=planning_policy.route_modes,
            max_walking_leg_meters=planning_policy.max_walking_leg_meters,
        )
        routes = {
            key: value for key, value in version.route_results.items() if key not in invalidated
        }
        delta = collect_route_delta(
            session.snapshot.trip,
            edited_draft,
            planning_pois,
            routes,
            route_strategy=planning_policy.route_strategy,
            route_modes=planning_policy.route_modes,
            max_walking_leg_meters=planning_policy.max_walking_leg_meters,
        )
        if delta.missing_queries:
            results = await tool_gateway.get_routes(
                list(delta.missing_queries), ToolCallContext(thread_id=session.lifecycle_thread_id)
            )
            for key, result in results.items():
                if result.status is ToolStatus.FAILED:
                    raise ToolUnavailableError.from_result(result, session.lifecycle_thread_id)
                assert result.data is not None
                routes[key] = result.data
        raw_candidate = materialize_candidates(
            session.snapshot.trip,
            [edited_draft],
            planning_pois,
            routes,
            route_strategy=planning_policy.route_strategy,
            route_modes=planning_policy.route_modes,
            max_walking_leg_meters=planning_policy.max_walking_leg_meters,
        )[0]
        days = []
        locked_item_ids = {
            lock.target_id for lock in session.locks.values() if lock.kind is LockKind.ITEM
        }
        for day in raw_candidate.days:
            items = [
                item.model_copy(
                    update={
                        "item_id": item_ids_by_poi.get(item.poi_id or ""),
                        "locked": item_ids_by_poi.get(item.poi_id or "") in locked_item_ids,
                    }
                )
                for item in day.items
            ]
            days.append(day.model_copy(update={"items": items}))
        candidate = raw_candidate.model_copy(update={"days": days})
        validation = validate_candidate(
            session.snapshot.trip, candidate, planning_pois
        )
        candidate = candidate.model_copy(update={"validation": validation})
        preview_id = f"P{len(session.previews) + 1}"
        impact = impact.model_copy(update={"invalidated_route_keys": invalidated})

        before_days = {day.date: day for day in version.candidate.days}
        after_days = {day.date: day for day in candidate.days}
        for preserved in impact.preserved_dates:
            if day_fingerprint(before_days[preserved]) != day_fingerprint(after_days[preserved]):
                logger.error("locality_guard.failed | session_id=%s day=%s", session.session_id, preserved)
                raise RuntimeError("unaffected day changed during local edit")
        for lock in session.locks.values():
            if lock.kind is LockKind.DAY:
                locked_day = after_days.get(date.fromisoformat(lock.target_id))
                if locked_day is None or day_fingerprint(locked_day) != lock.expected_fingerprint:
                    raise RuntimeError("locked day changed during local edit")
            else:
                located = next(
                    ((day.date, item) for day in candidate.days for item in day.items if item.item_id == lock.target_id),
                    None,
                )
                if located is None or item_fingerprint(located[1], located[0]) != lock.expected_fingerprint:
                    raise RuntimeError("locked item changed during local edit")
        logger.info(
            "locality_guard.completed | session_id=%s preserved_days=%s locked_items=%s",
            session.session_id,
            len(impact.preserved_dates),
            len(locked_item_ids),
        )

        critic_status = CriticStatus.DISABLED if critic_gateway is None else CriticStatus.PENDING
        critic_summary = None
        soft_critique = None
        soft_after = None
        if validation.status is not ValidationStatus.INVALID and critic_gateway is not None:
            digests = build_evidence_digests(
                session.snapshot.trip,
                [candidate],
                planning_pois,
                evidence_budget,
            )
            try:
                critic_result = await critic_gateway.critique(
                    SoftCriticRequest(digests=digests),
                    thread_id=session.lifecycle_thread_id,
                    grounding_attempt=1,
                )
                grounding_errors = validate_critique_grounding(
                    digests, critic_result.critiques
                )
                if grounding_errors:
                    critic_status = CriticStatus.INVALID_GROUNDING
                else:
                    critic_status = CriticStatus.SUCCESS
                    critic_summary = critic_result.summary
                    soft_critique = critic_result.critiques[0]
                    soft_after = quality_score(soft_critique, critic_policy)
            except ExecutionBudgetExceeded:
                critic_status = CriticStatus.UNAVAILABLE
                record_degradation("lifecycle_soft_critic_budget_exhausted")
            except CriticUnavailableError:
                critic_status = CriticStatus.UNAVAILABLE

        diff = build_plan_diff(
            version.candidate,
            candidate,
            from_version_id=version.version_id,
            to_id=preview_id,
            soft_quality_after=soft_after,
        )
        valid = validation.status is not ValidationStatus.INVALID and not any(
            item.severity is ViolationSeverity.ERROR for item in validation.violations
        )
        token = secrets.token_urlsafe(24) if valid else ""
        weather_event = (
            ChangeEvent.model_validate(state["weather_event"])
            if state["weather_event"] is not None
            else None
        )
        weather_snapshot = (
            WeatherSnapshot.model_validate(state["weather_snapshot"])
            if state["weather_snapshot"] is not None
            else None
        )
        change_trigger = PlanChangeTrigger(
            source=(
                ChangeSource.WEATHER
                if weather_event is not None
                else ChangeSource.USER
            ),
            request_id=str(request.request_id),
            event_id=weather_event.event_id if weather_event is not None else None,
            snapshot_id=(
                weather_snapshot.snapshot_id if weather_snapshot is not None else None
            ),
            policy_version=(
                WEATHER_POLICY_VERSION if weather_event is not None else None
            ),
        )
        preview = PlanPreview(
            preview_id=preview_id,
            base_version_id=version.version_id,
            base_session_revision=session.session_revision + 1,
            source_request_id=str(request.request_id),
            candidate=candidate,
            candidate_draft=edited_draft,
            planning_pois=tuple(planning_pois),
            route_results=routes,
            impact=impact,
            diff=diff,
            status=PreviewStatus.PENDING if valid else PreviewStatus.INVALID,
            hard_validation=validation,
            critic_status=critic_status,
            critic_summary=critic_summary,
            soft_critique=soft_critique,
            approval_token_hash=_approval_hash(token),
            change_trigger=change_trigger,
        )
        previous = session.session_revision
        if weather_event is not None and weather_snapshot is not None:
            weather_risks = tuple(
                DailyWeatherRisk.model_validate(item)
                for item in state["weather_risks"] or []
            )
            persist_weather_observation(
                session,
                snapshot=weather_snapshot,
                risks=weather_risks,
                outcome=(
                    WeatherRefreshOutcome.PREVIEW_CREATED
                    if valid
                    else WeatherRefreshOutcome.NEEDS_USER_ATTENTION
                ),
                event=weather_event,
                event_status=(
                    WeatherEventStatus.PREVIEW_CREATED
                    if valid
                    else WeatherEventStatus.NEEDS_USER_ATTENTION
                ),
                max_events=weather_max_events,
            )
        session.previews[preview_id] = preview
        session.pending_preview_id = preview_id
        session.session_revision += 1
        session.status = (
            PlanSessionStatus.AWAITING_CHANGE_APPROVAL
            if valid
            else PlanSessionStatus.CHANGE_REJECTED
        )
        session.receipts[str(request.request_id)] = _receipt(
            request,
            session.session_revision,
            preview_id=preview_id,
            event_id=weather_event.event_id if weather_event is not None else None,
        )
        if weather_event is not None:
            weather_receipt = session.weather_monitor.event_receipts[weather_event.event_id]
            weather_receipt.resulting_preview_id = preview_id
        await repository.save(session, expected_revision=previous)
        logger.info(
            "preview.hard_validated | session_id=%s preview_id=%s status=%s "
            "invalidated_routes=%s reused_routes=%s loaded_routes=%s",
            session.session_id,
            preview_id,
            validation.status.value,
            len(invalidated),
            len(delta.reused_route_keys),
            len(delta.missing_queries),
        )
        return {
            "status": session.status.value,
            "approval_token": token or None,
            "action": None,
            "message": None if valid else "修改后的计划未通过硬约束验证",
        }

    async def resolve_approval(state: PlanLifecycleState) -> dict:
        request = _request(state)
        session = await repository.get(state["session_id"])
        _ensure_expected(session, request)
        if session.status is not PlanSessionStatus.AWAITING_CHANGE_APPROVAL:
            raise LifecycleConflictError(session.session_id)
        preview = session.previews[cast(str, session.pending_preview_id)]
        if request.action.preview_id != preview.preview_id:  # type: ignore[union-attr]
            raise LifecycleConflictError(session.session_id, code="stale_preview")
        previous = session.session_revision
        if request.action.kind == "reject_preview":
            preview.status = PreviewStatus.REJECTED
            if (
                preview.change_trigger is not None
                and preview.change_trigger.event_id is not None
            ):
                weather_receipt = session.weather_monitor.event_receipts.get(
                    preview.change_trigger.event_id
                )
                if weather_receipt is not None:
                    weather_receipt.status = WeatherEventStatus.REJECTED
                    weather_receipt.resulting_preview_id = preview.preview_id
            session.status = PlanSessionStatus.ACTIVE
            session.session_revision += 1
            session.receipts[str(request.request_id)] = _receipt(
                request, session.session_revision, preview_id=preview.preview_id
            )
            await repository.save(session, expected_revision=previous)
            logger.info("preview.rejected | session_id=%s preview_id=%s", session.session_id, preview.preview_id)
            return {"status": session.status.value, "action": None}
        action = cast(ApprovalAction, request.action)
        if _approval_hash(action.approval_token) != preview.approval_token_hash:
            raise LifecycleConflictError(session.session_id, code="stale_approval_token")
        if preview.base_version_id != session.active_version_id or preview.base_session_revision != session.session_revision:
            preview.status = PreviewStatus.STALE
            raise LifecycleConflictError(session.session_id, code="stale_preview")
        if len(session.versions) >= max_versions:
            raise LifecycleActionError(session.session_id, "version_budget_exhausted", "计划版本数量已达到上限")
        number = len(session.versions) + 1
        version_id = f"V{number}"
        version = PlanVersion(
            version_id=version_id,
            number=number,
            parent_version_id=preview.base_version_id,
            source_request_id=str(request.request_id),
            selected_candidate_id=preview.candidate.id,
            candidate=preview.candidate,
            candidate_draft=preview.candidate_draft,
            planning_pois=preview.planning_pois,
            route_results=preview.route_results,
            plan_fingerprint=plan_fingerprint(preview.candidate),
            critic_status=preview.critic_status,
            change_trigger=preview.change_trigger,
        )
        preview.status = PreviewStatus.APPROVED
        session.versions[version_id] = version
        session.active_version_id = version_id
        session.status = PlanSessionStatus.ACTIVE
        session.session_revision += 1
        session.receipts[str(request.request_id)] = _receipt(
            request,
            session.session_revision,
            version_id=version_id,
            preview_id=preview.preview_id,
            event_id=(
                preview.change_trigger.event_id
                if preview.change_trigger is not None
                else None
            ),
        )
        if (
            preview.change_trigger is not None
            and preview.change_trigger.event_id is not None
        ):
            weather_receipt = session.weather_monitor.event_receipts.get(
                preview.change_trigger.event_id
            )
            if weather_receipt is not None:
                weather_receipt.status = WeatherEventStatus.APPROVED
                weather_receipt.resulting_preview_id = preview.preview_id
                weather_receipt.resulting_version_id = version_id
        await repository.save(session, expected_revision=previous)
        logger.info(
            "version.committed | session_id=%s version_id=%s parent_version_id=%s request_id=%s",
            session.session_id,
            version_id,
            version.parent_version_id,
            request.request_id,
        )
        return {"status": session.status.value, "action": None}

    builder = StateGraph(PlanLifecycleState)

    def add_node(name: str, function, *, terminal: bool = False) -> None:
        builder.add_node(
            name, instrument_node("lifecycle", name, function, terminal=terminal)
        )

    add_node("execution_budget_guard", execution_budget_guard)
    add_node("await_user_action", await_user_action, terminal=True)
    add_node("dispatch_action", dispatch_action)
    add_node("select_candidate", select_candidate)
    add_node("change_lock", change_lock)
    add_node("parse_edit", parse_edit)
    add_node("apply_edit_clarification", apply_edit_clarification)
    add_node("analyze_change_impact", analyze_impact)
    add_node("build_local_preview", build_preview)
    add_node("resolve_approval", resolve_approval)
    add_node("resolve_weather_location", resolve_weather_location)
    add_node("fetch_weather_snapshot", fetch_weather_snapshot)
    add_node("classify_weather_risks", classify_weather_risks)
    add_node("derive_weather_event", derive_weather_change)
    add_node("deduplicate_weather_event", deduplicate_weather_event)
    add_node("analyze_weather_impact", analyze_weather_change_impact)
    add_node("build_weather_repair_plan", build_weather_repair)
    add_node("dismiss_weather_event", dismiss_weather_event)
    builder.add_edge(START, "execution_budget_guard")
    builder.add_edge("execution_budget_guard", "await_user_action")
    builder.add_edge("await_user_action", "dispatch_action")
    builder.add_conditional_edges(
        "dispatch_action",
        instrument_route("lifecycle", "dispatch_action", route_action),
    )
    builder.add_edge("select_candidate", "await_user_action")
    builder.add_edge("change_lock", "await_user_action")
    builder.add_edge("parse_edit", "analyze_change_impact")
    builder.add_edge("apply_edit_clarification", "analyze_change_impact")
    builder.add_conditional_edges(
        "analyze_change_impact",
        instrument_route("lifecycle", "analyze_change_impact", route_impact),
        {"build_preview": "build_local_preview", "await_user_action": "await_user_action"},
    )
    builder.add_edge("build_local_preview", "await_user_action")
    builder.add_edge("resolve_approval", "await_user_action")
    builder.add_edge("resolve_weather_location", "fetch_weather_snapshot")
    builder.add_edge("fetch_weather_snapshot", "classify_weather_risks")
    builder.add_edge("classify_weather_risks", "derive_weather_event")
    builder.add_edge("derive_weather_event", "deduplicate_weather_event")
    builder.add_conditional_edges(
        "deduplicate_weather_event",
        instrument_route(
            "lifecycle", "deduplicate_weather_event", route_weather_event
        ),
        {
            "analyze_weather_impact": "analyze_weather_impact",
            "await_user_action": "await_user_action",
        },
    )
    builder.add_conditional_edges(
        "analyze_weather_impact",
        instrument_route(
            "lifecycle", "analyze_weather_impact", route_weather_impact
        ),
        {
            "build_weather_repair_plan": "build_weather_repair_plan",
            "await_user_action": "await_user_action",
        },
    )
    builder.add_conditional_edges(
        "build_weather_repair_plan",
        instrument_route(
            "lifecycle", "build_weather_repair_plan", route_weather_repair
        ),
        {
            "analyze_change_impact": "analyze_change_impact",
            "await_user_action": "await_user_action",
        },
    )
    builder.add_edge("dismiss_weather_event", "await_user_action")
    return builder.compile(
        checkpointer=checkpointer or ObservedCheckpointSaver(InMemorySaver())
    )


def initial_lifecycle_state(session: PlanSessionRecord) -> PlanLifecycleState:
    return {
        "session_id": session.session_id,
        "lifecycle_thread_id": session.lifecycle_thread_id,
        "status": session.status.value,
        "resume_value": None,
        "action": None,
        "edit_patch": None,
        "edit_summary": None,
        "impact_result": None,
        "weather_location": None,
        "weather_snapshot": None,
        "weather_risks": None,
        "weather_event": None,
        "weather_impact": None,
        "weather_repair_plan": None,
        "weather_alternatives": None,
        "weather_decision": None,
        "clarification_round": 0,
        "approval_token": None,
        "message": None,
        "transition_count": 0,
    }


def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 40}


def _allowed_actions(session: PlanSessionRecord) -> tuple[str, ...]:
    if session.status is PlanSessionStatus.AWAITING_CANDIDATE_SELECTION:
        return ("accept_recommendation", "select_candidate")
    if session.status is PlanSessionStatus.AWAITING_CHANGE_APPROVAL:
        return ("approve_preview", "reject_preview")
    if session.status is PlanSessionStatus.NEEDS_REQUIREMENT_CLARIFICATION:
        return ("clarify_requirement",)
    if session.status is PlanSessionStatus.NEEDS_EDIT_CLARIFICATION:
        return ("clarify_edit",)
    actions = ["lock", "unlock", "edit", "edit_text", "refresh_weather"]
    if session.weather_monitor.attention_event_id is not None:
        actions.append("dismiss_weather_event")
    return tuple(actions)


def response_from_session(
    session: PlanSessionRecord,
    *,
    interruptions: tuple[object, ...] = (),
    message: str | None = None,
    weather_stale_max_seconds: int = 21_600,
) -> PlanSessionResponse:
    interrupt_view = None
    if interruptions:
        item = interruptions[0]
        interrupt_view = LifecycleInterrupt(
            id=str(getattr(item, "id")), payload=dict(getattr(item, "value"))
        )
    elif session.external_interrupt is not None:
        interrupt_view = LifecycleInterrupt.model_validate(session.external_interrupt)
    return PlanSessionResponse(
        session_id=session.session_id,
        status=session.status,
        session_revision=session.session_revision,
        active_version=(
            session.versions.get(session.active_version_id)
            if session.active_version_id
            else None
        ),
        pending_preview=(
            session.previews.get(session.pending_preview_id)
            if session.pending_preview_id
            else None
        ),
        candidates=(session.snapshot.candidates if session.snapshot else ()),
        locks=tuple(session.locks.values()),
        allowed_actions=_allowed_actions(session),
        interrupt=interrupt_view,
        weather=weather_state_view(
            session, stale_max_seconds=weather_stale_max_seconds
        ),
        message=message,
    )


async def start_lifecycle(
    workflow: CompiledStateGraph,
    repository: PlanRepository,
    session: PlanSessionRecord,
    *,
    weather_stale_max_seconds: int = 21_600,
) -> PlanSessionResponse:
    result = await workflow.ainvoke(
        initial_lifecycle_state(session), config=_config(session.lifecycle_thread_id)
    )
    stored = await repository.get(session.session_id)
    return response_from_session(
        stored,
        interruptions=tuple(result.get("__interrupt__", ())),
        message=result.get("message"),
        weather_stale_max_seconds=weather_stale_max_seconds,
    )


async def resume_lifecycle(
    workflow: CompiledStateGraph,
    repository: PlanRepository,
    request: LifecycleResumeRequest,
    *,
    session_id: str,
    weather_stale_max_seconds: int = 21_600,
) -> PlanSessionResponse:
    session = await repository.get(session_id)
    existing = session.receipts.get(str(request.request_id))
    if existing is not None:
        snapshot = await workflow.aget_state(_config(session.lifecycle_thread_id))
        interruptions = tuple(
            value
            for task in snapshot.tasks
            for value in getattr(task, "interrupts", ())
        )
        logger.info(
            "lifecycle.action.replayed | session_id=%s request_id=%s action=%s",
            session_id,
            request.request_id,
            request.action.kind,
        )
        return response_from_session(
            session,
            interruptions=interruptions,
            weather_stale_max_seconds=weather_stale_max_seconds,
        )
    snapshot = await workflow.aget_state(_config(session.lifecycle_thread_id))
    if not snapshot.values:
        raise LifecycleNotFoundError(session_id)
    interruptions = tuple(
        value for task in snapshot.tasks for value in getattr(task, "interrupts", ())
    )
    if request.interrupt_id not in {str(getattr(value, "id", "")) for value in interruptions}:
        raise LifecycleConflictError(session_id, code="stale_interrupt")
    result = await workflow.ainvoke(
        Command(resume=request.model_dump(mode="json")),
        config=_config(session.lifecycle_thread_id),
    )
    stored = await repository.get(session_id)
    return response_from_session(
        stored,
        interruptions=tuple(result.get("__interrupt__", ())),
        message=result.get("message"),
        weather_stale_max_seconds=weather_stale_max_seconds,
    )
