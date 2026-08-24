from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from travel_agent.domain.models import PlanningRequest
from travel_agent.execution.models import RunStatus


class TripRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["trip-record-v1"] = "trip-record-v1"
    trip_id: str = Field(default_factory=lambda: str(uuid4()))
    tenant_id: str
    user_id: str
    request: PlanningRequest
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RunHandle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["run-handle-v1"] = "run-handle-v1"
    run_id: str
    trip_id: str
    thread_id: str
    status: RunStatus = RunStatus.RUNNING
    status_url: str
    events_url: str
    cancel_url: str
