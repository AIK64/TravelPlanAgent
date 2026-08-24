from __future__ import annotations

from enum import StrEnum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from travel_agent.memory.models import AgentRole


class HandoffReason(StrEnum):
    PLAN = "plan"
    SOFT_CRITIQUE = "soft_critique"
    HARD_REPAIR = "hard_repair"
    SOFT_REPAIR = "soft_repair"


class SpecialistStatus(StrEnum):
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class AgentBudget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_context_characters: int = Field(default=40_000, ge=128, le=1_000_000)
    max_output_characters: int = Field(default=120_000, ge=128, le=5_000_000)
    deadline_ms: int = Field(default=30_000, ge=100, le=600_000)


class AgentHandoff(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["agent-handoff-v1"] = "agent-handoff-v1"
    handoff_id: str = Field(default_factory=lambda: str(uuid4()))
    parent_run_id: str | None = None
    from_role: Literal["orchestrator"] = "orchestrator"
    to_role: AgentRole
    reason: HandoffReason
    input_schema: str
    input_hash: str = Field(min_length=16, max_length=128)
    expected_output_schema: str
    context_characters: int = Field(ge=0)
    budget: AgentBudget
    idempotency_key: str = Field(min_length=16, max_length=128)


class SpecialistExecutionSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    handoff_id: str
    role: AgentRole
    reason: HandoffReason
    status: SpecialistStatus
    elapsed_ms: int = Field(ge=0)
    context_characters: int = Field(ge=0)
    output_characters: int = Field(ge=0)
    output_hash: str | None = Field(default=None, min_length=16, max_length=128)
    error_code: str | None = None
