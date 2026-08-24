from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class RepairActionKind(StrEnum):
    REMOVE_OPTIONAL_POI = "remove_optional_poi"
    MOVE_POI = "move_poi"
    REORDER_OPTIONAL_POI = "reorder_optional_poi"
    INSERT_MUST_VISIT = "insert_must_visit"
    ADD_AVAILABLE_POI = "add_available_poi"


class RepairOutcome(StrEnum):
    RESOLVED = "resolved"
    IMPROVED = "improved"
    NO_PROGRESS = "no_progress"


class RepairAction(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: RepairActionKind
    source_violation_type: str
    poi_id: str
    from_day: date | None = None
    to_day: date | None = None
    reason: str
    expected_effect: str


class CriticReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate_id: str
    violation_fingerprint: str
    error_count: int = Field(ge=0)
    warning_count: int = Field(ge=0)
    violation_types: tuple[str, ...] = ()
    affected_days: tuple[date, ...] = ()
    affected_poi_ids: tuple[str, ...] = ()
    repairable: bool
    terminal_reason: str | None = None


class RepairPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    round: int = Field(ge=1)
    target_candidate_id: str
    source_violation_types: tuple[str, ...]
    actions: tuple[RepairAction, ...]
    affected_days: tuple[date, ...]
    preserved_days: tuple[date, ...]
    invalidated_route_keys: tuple[str, ...] = ()
    expected_effects: tuple[str, ...]
    action_fingerprint: str


class RepairAttempt(BaseModel):
    model_config = ConfigDict(frozen=True)

    round: int = Field(ge=1)
    target_candidate_id: str
    before_violation_fingerprint: str
    after_violation_fingerprint: str
    before_error_count: int = Field(ge=0)
    after_error_count: int = Field(ge=0)
    action_fingerprint: str
    action_kinds: tuple[RepairActionKind, ...]
    outcome: RepairOutcome
    affected_days: tuple[date, ...]
    preserved_day_count: int = Field(ge=0)
    reused_route_count: int = Field(ge=0)
    loaded_route_count: int = Field(ge=0)
    terminal_reason: str | None = None
