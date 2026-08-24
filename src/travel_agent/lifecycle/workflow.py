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
    EditModelInput,
    EditClarificationAction,
    EditPatch,
    ImpactScope,
    LifecycleInterrupt,
    LifecycleResumeRequest,
    LockAction,
    LockKind,
    PlanLock,
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
from travel_agent.domain.models import ValidationStatus, ViolationSeverity
from travel_agent.domain.tool_models import ToolCallContext, ToolStatus, route_key
from travel_agent.domain.tool_models import POISearchQuery
from travel_agent.edits.gateway import EditGateway
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
) -> ActionReceipt:
    return ActionReceipt(
        request_id=str(request.request_id),
        action_kind=request.action.kind,
        resulting_revision=revision,
        resulting_version_id=version_id,
        resulting_preview_id=preview_id,
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
                "allowed_actions": ["lock", "unlock", "edit", "edit_text"],
            }
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
        }

    def route_action(
        state: PlanLifecycleState,
    ) -> Literal[
        "select_candidate",
        "change_lock",
        "parse_edit",
        "apply_edit_clarification",
        "resolve_approval",
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
        raise LifecycleActionError(state["session_id"], "invalid_action", "当前阶段不支持该动作")

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
        )
        previous = session.session_revision
        session.previews[preview_id] = preview
        session.pending_preview_id = preview_id
        session.session_revision += 1
        session.status = (
            PlanSessionStatus.AWAITING_CHANGE_APPROVAL
            if valid
            else PlanSessionStatus.CHANGE_REJECTED
        )
        session.receipts[str(request.request_id)] = _receipt(
            request, session.session_revision, preview_id=preview_id
        )
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
        )
        preview.status = PreviewStatus.APPROVED
        session.versions[version_id] = version
        session.active_version_id = version_id
        session.status = PlanSessionStatus.ACTIVE
        session.session_revision += 1
        session.receipts[str(request.request_id)] = _receipt(
            request, session.session_revision, version_id=version_id, preview_id=preview.preview_id
        )
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
    builder.add_node("await_user_action", await_user_action)
    builder.add_node("dispatch_action", dispatch_action)
    builder.add_node("select_candidate", select_candidate)
    builder.add_node("change_lock", change_lock)
    builder.add_node("parse_edit", parse_edit)
    builder.add_node("apply_edit_clarification", apply_edit_clarification)
    builder.add_node("analyze_change_impact", analyze_impact)
    builder.add_node("build_local_preview", build_preview)
    builder.add_node("resolve_approval", resolve_approval)
    builder.add_edge(START, "await_user_action")
    builder.add_edge("await_user_action", "dispatch_action")
    builder.add_conditional_edges("dispatch_action", route_action)
    builder.add_edge("select_candidate", "await_user_action")
    builder.add_edge("change_lock", "await_user_action")
    builder.add_edge("parse_edit", "analyze_change_impact")
    builder.add_edge("apply_edit_clarification", "analyze_change_impact")
    builder.add_conditional_edges(
        "analyze_change_impact",
        route_impact,
        {"build_preview": "build_local_preview", "await_user_action": "await_user_action"},
    )
    builder.add_edge("build_local_preview", "await_user_action")
    builder.add_edge("resolve_approval", "await_user_action")
    return builder.compile(checkpointer=checkpointer or InMemorySaver())


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
    return ("lock", "unlock", "edit", "edit_text")


def response_from_session(
    session: PlanSessionRecord,
    *,
    interruptions: tuple[object, ...] = (),
    message: str | None = None,
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
        message=message,
    )


async def start_lifecycle(
    workflow: CompiledStateGraph,
    repository: PlanRepository,
    session: PlanSessionRecord,
) -> PlanSessionResponse:
    result = await workflow.ainvoke(
        initial_lifecycle_state(session), config=_config(session.lifecycle_thread_id)
    )
    stored = await repository.get(session.session_id)
    return response_from_session(
        stored,
        interruptions=tuple(result.get("__interrupt__", ())),
        message=result.get("message"),
    )


async def resume_lifecycle(
    workflow: CompiledStateGraph,
    repository: PlanRepository,
    request: LifecycleResumeRequest,
    *,
    session_id: str,
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
        return response_from_session(session, interruptions=interruptions)
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
    )
