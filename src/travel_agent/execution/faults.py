from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class FaultPoint(StrEnum):
    REQUIREMENT_LLM = "requirement_llm"
    CLARIFICATION_LLM = "clarification_llm"
    EDIT_LLM = "edit_llm"
    CRITIC_LLM = "critic_llm"
    POI_PROVIDER = "poi_provider"
    ROUTE_PROVIDER = "route_provider"
    WEATHER_PROVIDER = "weather_provider"
    CHECKPOINT_READ = "checkpoint_read"
    CHECKPOINT_WRITE = "checkpoint_write"
    PLAN_REPOSITORY = "plan_repository"
    TRACE_SINK = "trace_sink"


class FaultMode(StrEnum):
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    AUTH_ERROR = "auth_error"
    CONNECTION_ERROR = "connection_error"
    INVALID_SCHEMA = "invalid_schema"
    EMPTY_BUSINESS_RESULT = "empty_business_result"
    WRITE_FAILURE = "write_failure"


class FaultRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    point: FaultPoint
    mode: FaultMode
    operation: str | None = None
    trigger_attempt: int = Field(default=1, ge=1)
    times: int = Field(default=1, ge=1, le=100)


class FaultPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rules: tuple[FaultRule, ...] = ()


class FaultInjector:
    """只由测试/评测代码显式注入的确定性故障计划。"""

    def __init__(self, plan: FaultPlan | None = None) -> None:
        self.plan = plan or FaultPlan()
        self._hits: dict[int, int] = {}

    def match(
        self, point: FaultPoint, *, operation: str | None = None, attempt: int = 1
    ) -> FaultMode | None:
        for index, rule in enumerate(self.plan.rules):
            if rule.point is not point or attempt < rule.trigger_attempt:
                continue
            if rule.operation is not None and rule.operation != operation:
                continue
            hits = self._hits.get(index, 0)
            if hits >= rule.times:
                continue
            self._hits[index] = hits + 1
            return rule.mode
        return None
