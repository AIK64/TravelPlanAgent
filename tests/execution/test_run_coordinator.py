from __future__ import annotations

from pydantic import BaseModel
import pytest

from travel_agent.execution.coordinator import RunCoordinator
from travel_agent.execution.models import (
    ExecutionBudget,
    RunKind,
    RunStatus,
    RunTerminalReason,
    TraceEventType,
)
from travel_agent.execution.repository import InMemoryRunRepository


class Payload(BaseModel):
    status: str
    interrupt: dict | None = None


@pytest.mark.asyncio
async def test_coordinator_persists_completed_run_and_trace():
    repository = InMemoryRunRepository()
    coordinator = RunCoordinator(repository, ExecutionBudget())

    result = await coordinator.execute(
        RunKind.STRUCTURED_PLAN,
        lambda: _payload("completed"),
        thread_id="thread-1",
    )

    assert result.run is not None
    assert result.run.status is RunStatus.COMPLETED
    assert result.run.terminal_reason is RunTerminalReason.PLAN_COMPLETED
    events = await repository.trace(result.run.run_id)
    assert events[0].event_type is TraceEventType.RUN_STARTED
    assert events[-1].event_type is TraceEventType.RUN_COMPLETED


@pytest.mark.asyncio
async def test_coordinator_marks_interrupt_and_idempotent_replay():
    repository = InMemoryRunRepository()
    coordinator = RunCoordinator(repository, ExecutionBudget())
    first = await coordinator.execute(
        RunKind.CLARIFICATION_RESUME,
        lambda: _payload("needs_clarification", interrupt={"id": "i1"}),
        thread_id="thread-2",
        request_id="request-1",
    )
    second = await coordinator.execute(
        RunKind.CLARIFICATION_RESUME,
        lambda: _payload("needs_clarification", interrupt={"id": "i1"}),
        thread_id="thread-2",
        request_id="request-1",
    )

    assert first.run is not None and second.run is not None
    assert first.run.status is RunStatus.INTERRUPTED
    assert second.run.status is RunStatus.REPLAYED
    assert second.run.replay_of_run_id == first.run.run_id


async def _payload(status: str, interrupt: dict | None = None) -> Payload:
    return Payload(status=status, interrupt=interrupt)
