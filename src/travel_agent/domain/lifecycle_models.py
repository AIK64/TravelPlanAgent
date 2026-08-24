from __future__ import annotations

from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from travel_agent.domain.critique_models import (
    CriticExecutionSummary,
    CriticStatus,
    SoftCritique,
)
from travel_agent.domain.models import (
    PlanCandidate,
    PlanningPOI,
    TripSpec,
    ValidationResult,
)
from travel_agent.domain.tool_models import RouteResult, ToolExecutionSummary
from travel_agent.planning.drafts import CandidateDraft


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PlanSessionStatus(StrEnum):
    NEEDS_REQUIREMENT_CLARIFICATION = "needs_requirement_clarification"
    AWAITING_CANDIDATE_SELECTION = "awaiting_candidate_selection"
    ACTIVE = "active"
    NEEDS_EDIT_CLARIFICATION = "needs_edit_clarification"
    BUILDING_PREVIEW = "building_preview"
    AWAITING_CHANGE_APPROVAL = "awaiting_change_approval"
    CHANGE_REJECTED = "change_rejected"
    REQUIRES_NEW_PLAN = "requires_new_plan"


class LockKind(StrEnum):
    DAY = "day"
    ITEM = "item"


class EditOperationKind(StrEnum):
    MOVE_ITEM = "move_item"
    REORDER_ITEM = "reorder_item"
    REMOVE_ITEM = "remove_item"
    ADD_ITEM = "add_item"
    REPLACE_ITEM = "replace_item"


class ImpactScope(StrEnum):
    ITEM = "item"
    DAY = "day"
    MULTI_DAY = "multi_day"
    REQUIRES_NEW_PLAN = "requires_new_plan"


class PreviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    STALE = "stale"
    INVALID = "invalid"


class PlanLock(BaseModel):
    model_config = ConfigDict(frozen=True)

    lock_id: str
    kind: LockKind
    target_id: str
    expected_fingerprint: str
    created_by_request_id: str
    created_at: datetime = Field(default_factory=utcnow)


class EditOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: EditOperationKind
    item_id: str | None = None
    item_name: str | None = None
    target_date: date | None = None
    target_index: int | None = Field(default=None, ge=0, le=20)
    poi_name: str | None = None
    user_reason: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def validate_operation(self) -> "EditOperation":
        if self.kind in {
            EditOperationKind.MOVE_ITEM,
            EditOperationKind.REORDER_ITEM,
            EditOperationKind.REMOVE_ITEM,
            EditOperationKind.REPLACE_ITEM,
        } and not (self.item_id or self.item_name):
            raise ValueError("edit operation requires item_id or item_name")
        if self.kind in {
            EditOperationKind.MOVE_ITEM,
            EditOperationKind.ADD_ITEM,
        } and self.target_date is None:
            raise ValueError("move/add operation requires target_date")
        if self.kind in {
            EditOperationKind.ADD_ITEM,
            EditOperationKind.REPLACE_ITEM,
        } and not self.poi_name:
            raise ValueError("add/replace operation requires poi_name")
        return self


class EditPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operations: tuple[EditOperation, ...] = Field(min_length=1, max_length=3)


class EditItemContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    item_id: str
    poi_id: str | None
    name: str
    day: date
    index: int
    locked: bool = False


class EditModelInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1, max_length=2_000)
    trip_start: date
    trip_end: date
    items: tuple[EditItemContext, ...] = Field(max_length=80)


class EditProviderOutput(BaseModel):
    patch: EditPatch
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class EditExecutionSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    model: str
    prompt_version: str
    attempt_count: int = Field(ge=1)
    elapsed_ms: float = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class ImpactResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    scope: ImpactScope
    affected_dates: tuple[date, ...] = ()
    affected_item_ids: tuple[str, ...] = ()
    preserved_dates: tuple[date, ...] = ()
    invalidated_route_keys: tuple[str, ...] = ()
    required_tool_operations: tuple[str, ...] = ()
    lock_conflicts: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()


class ItemDiff(BaseModel):
    model_config = ConfigDict(frozen=True)

    item_id: str
    name: str
    from_date: date | None = None
    to_date: date | None = None
    from_index: int | None = None
    to_index: int | None = None


class TimeDiff(BaseModel):
    model_config = ConfigDict(frozen=True)

    item_id: str
    before_start: datetime
    after_start: datetime
    before_end: datetime
    after_end: datetime


class RouteDiff(BaseModel):
    model_config = ConfigDict(frozen=True)

    item_id: str
    before_minutes: int
    after_minutes: int
    before_meters: int
    after_meters: int


class DayMetricDiff(BaseModel):
    model_config = ConfigDict(frozen=True)

    day: date
    travel_minutes_delta: int
    walking_meters_delta: int
    known_cost_delta: float


class PlanDiff(BaseModel):
    model_config = ConfigDict(frozen=True)

    from_version_id: str
    to_id: str
    added_items: tuple[ItemDiff, ...] = ()
    removed_items: tuple[ItemDiff, ...] = ()
    moved_items: tuple[ItemDiff, ...] = ()
    reordered_items: tuple[ItemDiff, ...] = ()
    time_changes: tuple[TimeDiff, ...] = ()
    route_changes: tuple[RouteDiff, ...] = ()
    day_metric_changes: tuple[DayMetricDiff, ...] = ()
    hard_status_before: str | None = None
    hard_status_after: str | None = None
    soft_quality_before: float | None = None
    soft_quality_after: float | None = None


class PlanningSnapshot(BaseModel):
    """生命周期所需的标准化规划事实；不包含 Provider 原始响应。"""

    trip: TripSpec
    candidates: tuple[PlanCandidate, ...]
    recommended_candidate_id: str
    candidate_drafts: tuple[CandidateDraft, ...]
    planning_pois: tuple[PlanningPOI, ...]
    route_results: dict[str, RouteResult]
    critic_status: CriticStatus = CriticStatus.NOT_RUN


class PlanVersion(BaseModel):
    model_config = ConfigDict(frozen=True)

    version_id: str
    number: int = Field(ge=1)
    parent_version_id: str | None = None
    source_request_id: str
    selected_candidate_id: str
    candidate: PlanCandidate
    candidate_draft: CandidateDraft
    planning_pois: tuple[PlanningPOI, ...]
    route_results: dict[str, RouteResult]
    plan_fingerprint: str
    critic_status: CriticStatus = CriticStatus.NOT_RUN
    created_at: datetime = Field(default_factory=utcnow)


class PlanPreview(BaseModel):
    preview_id: str
    base_version_id: str
    base_session_revision: int = Field(ge=0)
    source_request_id: str
    candidate: PlanCandidate
    candidate_draft: CandidateDraft
    planning_pois: tuple[PlanningPOI, ...]
    route_results: dict[str, RouteResult]
    impact: ImpactResult
    diff: PlanDiff
    status: PreviewStatus = PreviewStatus.PENDING
    hard_validation: ValidationResult
    critic_status: CriticStatus = CriticStatus.NOT_RUN
    critic_summary: CriticExecutionSummary | None = None
    soft_critique: SoftCritique | None = None
    approval_token_hash: str
    created_at: datetime = Field(default_factory=utcnow)


class ActionReceipt(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str
    action_kind: str
    resulting_revision: int = Field(ge=0)
    resulting_version_id: str | None = None
    resulting_preview_id: str | None = None


class PlanSessionRecord(BaseModel):
    session_id: str
    lifecycle_thread_id: str
    intake_thread_id: str | None = None
    status: PlanSessionStatus
    session_revision: int = Field(default=0, ge=0)
    snapshot: PlanningSnapshot | None = None
    active_version_id: str | None = None
    pending_preview_id: str | None = None
    versions: dict[str, PlanVersion] = Field(default_factory=dict)
    previews: dict[str, PlanPreview] = Field(default_factory=dict)
    locks: dict[str, PlanLock] = Field(default_factory=dict)
    receipts: dict[str, ActionReceipt] = Field(default_factory=dict)
    external_interrupt: dict | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class AcceptRecommendationAction(BaseModel):
    kind: Literal["accept_recommendation"] = "accept_recommendation"


class SelectCandidateAction(BaseModel):
    kind: Literal["select_candidate"] = "select_candidate"
    candidate_id: str


class LockAction(BaseModel):
    kind: Literal["lock", "unlock"]
    lock_kind: LockKind
    target_id: str


class StructuredEditAction(BaseModel):
    kind: Literal["edit"] = "edit"
    patch: EditPatch


class TextEditAction(BaseModel):
    kind: Literal["edit_text"] = "edit_text"
    text: str = Field(min_length=1, max_length=2_000)

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("text must not be blank")
        return normalized


class ApprovalAction(BaseModel):
    kind: Literal["approve_preview"] = "approve_preview"
    preview_id: str
    approval_token: str = Field(min_length=16, max_length=512)


class RejectAction(BaseModel):
    kind: Literal["reject_preview"] = "reject_preview"
    preview_id: str


class RequirementClarificationAction(BaseModel):
    kind: Literal["clarify_requirement"] = "clarify_requirement"
    answer: str = Field(min_length=1, max_length=2_000)


class EditClarificationAction(BaseModel):
    kind: Literal["clarify_edit"] = "clarify_edit"
    item_id: str = Field(min_length=1, max_length=256)


LifecycleAction = Annotated[
    AcceptRecommendationAction
    | SelectCandidateAction
    | LockAction
    | StructuredEditAction
    | TextEditAction
    | ApprovalAction
    | RejectAction
    | RequirementClarificationAction
    | EditClarificationAction,
    Field(discriminator="kind"),
]


class LifecycleResumeRequest(BaseModel):
    interrupt_id: str = Field(min_length=1, max_length=256)
    request_id: UUID
    expected_active_version_id: str | None = None
    expected_session_revision: int | None = Field(default=None, ge=0)
    action: LifecycleAction


class LifecycleInterrupt(BaseModel):
    id: str
    payload: dict


class PlanSessionResponse(BaseModel):
    session_id: str
    status: PlanSessionStatus
    session_revision: int
    active_version: PlanVersion | None = None
    pending_preview: PlanPreview | None = None
    candidates: tuple[PlanCandidate, ...] = ()
    locks: tuple[PlanLock, ...] = ()
    allowed_actions: tuple[str, ...] = ()
    interrupt: LifecycleInterrupt | None = None
    message: str | None = None
