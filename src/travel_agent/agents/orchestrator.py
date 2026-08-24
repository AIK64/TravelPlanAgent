from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from hashlib import sha256
import inspect
import json
from time import perf_counter
from typing import TypeVar

from pydantic import BaseModel

from travel_agent.agents.contracts import (
    AgentBudget,
    AgentHandoff,
    HandoffReason,
    SpecialistExecutionSummary,
    SpecialistStatus,
)
from travel_agent.agents.errors import (
    SpecialistContextRejected,
    SpecialistTimeout,
)
from travel_agent.execution.context import current_run_context
from travel_agent.execution.models import TraceEventType
from travel_agent.memory.models import AgentRole


T = TypeVar("T")


class SpecialistExecutor:
    """进程内强类型 Handoff 执行器；不拥有主 Graph State。"""

    def __init__(
        self,
        *,
        max_handoffs: int = 8,
        default_budget: AgentBudget | None = None,
    ) -> None:
        if not 1 <= max_handoffs <= 100:
            raise ValueError("max_handoffs must be between 1 and 100")
        self.max_handoffs = max_handoffs
        self.default_budget = default_budget or AgentBudget()

    async def invoke(
        self,
        *,
        role: AgentRole,
        reason: HandoffReason,
        context: BaseModel,
        expected_output_schema: str,
        operation: Callable[[], T | Awaitable[T]],
        budget: AgentBudget | None = None,
    ) -> tuple[T, SpecialistExecutionSummary]:
        resolved_budget = budget or self.default_budget
        context_json = context.model_dump_json()
        context_characters = len(context_json)
        if context_characters > resolved_budget.max_context_characters:
            self._trace_rejected(
                role,
                reason,
                "context_budget_exceeded",
                context_characters,
            )
            raise SpecialistContextRejected(
                "context_budget_exceeded",
                "specialist context exceeds its character budget",
            )
        run = current_run_context()
        if run is not None:
            started_count = sum(
                event.event_type is TraceEventType.AGENT_HANDOFF_STARTED
                for event in run.trace.events
            )
            if started_count >= self.max_handoffs:
                self._trace_rejected(
                    role, reason, "handoff_budget_exceeded", context_characters
                )
                raise SpecialistContextRejected(
                    "handoff_budget_exceeded",
                    "specialist handoff budget is exhausted",
                )
        input_hash = sha256(context_json.encode("utf-8")).hexdigest()
        parent_run_id = run.record.run_id if run is not None else "no-run"
        idempotency_key = sha256(
            f"{parent_run_id}:{role.value}:"
            f"{reason.value}:{input_hash}".encode("utf-8")
        ).hexdigest()
        handoff = AgentHandoff(
            parent_run_id=run.record.run_id if run is not None else None,
            to_role=role,
            reason=reason,
            input_schema=context.__class__.__name__,
            input_hash=input_hash,
            expected_output_schema=expected_output_schema,
            context_characters=context_characters,
            budget=resolved_budget,
            idempotency_key=idempotency_key,
        )
        parent_event_id = self._trace_started(handoff)
        started = perf_counter()
        try:
            result = operation()
            if inspect.isawaitable(result):
                result = await asyncio.wait_for(
                    result, timeout=resolved_budget.deadline_ms / 1000
                )
        except TimeoutError as error:
            elapsed_ms = round((perf_counter() - started) * 1000)
            self._trace_failed(
                handoff,
                parent_event_id,
                SpecialistStatus.TIMED_OUT,
                "specialist_timeout",
                elapsed_ms,
            )
            raise SpecialistTimeout(
                "specialist_timeout", "specialist execution timed out"
            ) from error
        except BaseException:
            elapsed_ms = round((perf_counter() - started) * 1000)
            self._trace_failed(
                handoff,
                parent_event_id,
                SpecialistStatus.FAILED,
                "specialist_failed",
                elapsed_ms,
            )
            raise

        output_json = self._serialize_output(result)
        if len(output_json) > resolved_budget.max_output_characters:
            self._trace_failed(
                handoff,
                parent_event_id,
                SpecialistStatus.REJECTED,
                "output_budget_exceeded",
                round((perf_counter() - started) * 1000),
            )
            raise SpecialistContextRejected(
                "output_budget_exceeded",
                "specialist output exceeds its character budget",
            )
        summary = SpecialistExecutionSummary(
            handoff_id=handoff.handoff_id,
            role=role,
            reason=reason,
            status=SpecialistStatus.COMPLETED,
            elapsed_ms=round((perf_counter() - started) * 1000),
            context_characters=context_characters,
            output_characters=len(output_json),
            output_hash=sha256(output_json.encode("utf-8")).hexdigest(),
        )
        self._trace_completed(handoff, summary, parent_event_id)
        return result, summary

    @staticmethod
    def _serialize_output(value: object) -> str:
        if isinstance(value, BaseModel):
            return value.model_dump_json()
        if isinstance(value, (list, tuple)):
            normalized = [
                item.model_dump(mode="json") if isinstance(item, BaseModel) else item
                for item in value
            ]
            return json.dumps(
                normalized, ensure_ascii=False, sort_keys=True, default=str
            )
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)

    @staticmethod
    def _trace_started(handoff: AgentHandoff) -> str | None:
        run = current_run_context()
        if run is None:
            return None
        event = run.trace.record(
            TraceEventType.AGENT_HANDOFF_STARTED,
            status="started",
            operation=handoff.reason.value,
            attributes={
                "agent_role": handoff.to_role.value,
                "handoff_id": handoff.handoff_id,
                "input_schema": handoff.input_schema,
                "context_characters": handoff.context_characters,
            },
        )
        return event.event_id if event is not None else None

    @staticmethod
    def _trace_completed(
        handoff: AgentHandoff,
        summary: SpecialistExecutionSummary,
        parent_event_id: str | None,
    ) -> None:
        run = current_run_context()
        if run is None:
            return
        run.trace.record(
            TraceEventType.AGENT_HANDOFF_COMPLETED,
            status="completed",
            operation=handoff.reason.value,
            parent_event_id=parent_event_id,
            duration_ms=summary.elapsed_ms,
            attributes={
                "agent_role": handoff.to_role.value,
                "handoff_id": handoff.handoff_id,
                "output_schema": handoff.expected_output_schema,
                "output_characters": summary.output_characters,
            },
        )

    @staticmethod
    def _trace_failed(
        handoff: AgentHandoff,
        parent_event_id: str | None,
        status: SpecialistStatus,
        code: str,
        elapsed_ms: int,
    ) -> None:
        run = current_run_context()
        if run is None:
            return
        run.trace.record(
            TraceEventType.AGENT_HANDOFF_REJECTED,
            status=status.value,
            operation=handoff.reason.value,
            parent_event_id=parent_event_id,
            duration_ms=elapsed_ms,
            attributes={
                "agent_role": handoff.to_role.value,
                "handoff_id": handoff.handoff_id,
                "code": code,
            },
        )

    @staticmethod
    def _trace_rejected(
        role: AgentRole,
        reason: HandoffReason,
        code: str,
        context_characters: int,
    ) -> None:
        run = current_run_context()
        if run is None:
            return
        run.trace.record(
            TraceEventType.AGENT_HANDOFF_REJECTED,
            status="rejected",
            operation=reason.value,
            attributes={
                "agent_role": role.value,
                "code": code,
                "context_characters": context_characters,
            },
        )
