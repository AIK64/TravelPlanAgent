from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from travel_agent.domain.models import PlanStyle
from travel_agent.domain.repair_models import RepairAction


class EvidenceKind(StrEnum):
    TRIP_PREFERENCE = "trip_preference"
    CANDIDATE_METRIC = "candidate_metric"
    DAY_METRIC = "day_metric"
    POI_FACT = "poi_fact"
    SCHEDULE_FACT = "schedule_fact"
    ROUTE_FACT = "route_fact"
    ASSUMPTION = "assumption"


class EvidenceItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=8, max_length=64)
    kind: EvidenceKind
    candidate_id: str
    day: date | None = None
    entity_id: str | None = None
    field: str
    value: str | int | float | bool
    source: str
    confidence: float = Field(ge=0, le=1)


class CandidateEvidenceDigest(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal["critic-evidence-v1"] = "critic-evidence-v1"
    candidate_id: str
    style: PlanStyle
    evidence: tuple[EvidenceItem, ...]
    input_chars: int = Field(ge=0)
    truncated: bool = False


class SoftDimension(StrEnum):
    PACE = "pace"
    INTEREST_COVERAGE = "interest_coverage"
    DIVERSITY = "diversity"
    REST_FRIENDLINESS = "rest_friendliness"
    GEOGRAPHIC_COHERENCE = "geographic_coherence"


class SuggestedActionKind(StrEnum):
    MOVE_OPTIONAL_POI = "move_optional_poi"
    REORDER_OPTIONAL_POI = "reorder_optional_poi"
    REMOVE_OPTIONAL_POI = "remove_optional_poi"
    NO_ACTION = "no_action"


class SuggestedSoftAction(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: SuggestedActionKind
    poi_id: str | None = None
    from_day: date | None = None
    to_day: date | None = None
    evidence_ids: tuple[str, ...] = ()
    expected_dimension: SoftDimension | None = None

    @model_validator(mode="after")
    def validate_field_combination(self) -> "SuggestedSoftAction":
        if self.kind is SuggestedActionKind.NO_ACTION:
            if any((self.poi_id, self.from_day, self.to_day)):
                raise ValueError("no_action must not identify a POI or date")
            return self
        if self.poi_id is None or self.from_day is None:
            raise ValueError("a soft action requires poi_id and from_day")
        if self.kind is SuggestedActionKind.MOVE_OPTIONAL_POI:
            if self.to_day is None or self.to_day == self.from_day:
                raise ValueError("move_optional_poi requires a different to_day")
        elif self.to_day is not None and self.to_day != self.from_day:
            raise ValueError("non-move actions cannot target another day")
        return self


class DimensionCritique(BaseModel):
    model_config = ConfigDict(frozen=True)

    dimension: SoftDimension
    score: int = Field(ge=0, le=100)
    summary: str = Field(min_length=1, max_length=400)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=12)
    suggested_action: SuggestedSoftAction | None = None


class SoftCritique(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate_id: str
    dimensions: tuple[DimensionCritique, ...] = Field(min_length=1, max_length=5)
    overall_summary: str = Field(min_length=1, max_length=800)
    tradeoff_evidence_ids: tuple[str, ...] = Field(default=(), max_length=12)


class CriticStatus(StrEnum):
    NOT_RUN = "not_run"
    PENDING = "pending"
    SUCCESS = "success"
    UNAVAILABLE = "unavailable"
    INVALID_GROUNDING = "invalid_grounding"
    DISABLED = "disabled"


class CriticExecutionSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    model: str
    prompt_version: str
    status: CriticStatus
    attempt_count: int = Field(ge=0)
    grounding_attempt_count: int = Field(ge=0)
    elapsed_ms: float = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    input_chars: int = Field(ge=0)
    error_category: str | None = None


class SoftRepairPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    round: int = Field(ge=1)
    target_candidate_id: str
    source_dimension: SoftDimension
    source_evidence_ids: tuple[str, ...]
    action: RepairAction
    affected_days: tuple[date, ...]
    preserved_days: tuple[date, ...]
    action_fingerprint: str


class SoftRepairAttempt(BaseModel):
    model_config = ConfigDict(frozen=True)

    round: int = Field(ge=1)
    before_quality_score: float = Field(ge=0, le=100)
    after_quality_score: float | None = Field(default=None, ge=0, le=100)
    hard_validation_passed: bool
    accepted: bool
    reused_route_count: int = Field(ge=0)
    loaded_route_count: int = Field(ge=0)
    terminal_reason: str


class GroundedStatement(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1, max_length=500)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=12)


class GroundedExplanation(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate_id: str
    headline: str = Field(min_length=1, max_length=300)
    highlights: tuple[GroundedStatement, ...] = Field(default=(), max_length=5)
    tradeoffs: tuple[GroundedStatement, ...] = Field(default=(), max_length=5)
    critic_status: CriticStatus


class SoftCriticRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    digests: tuple[CandidateEvidenceDigest, ...] = Field(min_length=1, max_length=3)
    grounding_feedback: tuple[str, ...] = Field(default=(), max_length=20)

    @property
    def input_chars(self) -> int:
        return sum(digest.input_chars for digest in self.digests)


class SoftCriticProviderOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    critiques: tuple[SoftCritique, ...]
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class SoftCriticResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    critiques: tuple[SoftCritique, ...]
    summary: CriticExecutionSummary


from travel_agent.domain.models import rebuild_critique_response_model  # noqa: E402


rebuild_critique_response_model(
    critic_status=CriticStatus,
    execution_summary=CriticExecutionSummary,
    soft_critique=SoftCritique,
    grounded_explanation=GroundedExplanation,
)
