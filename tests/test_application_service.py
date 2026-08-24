from __future__ import annotations

import asyncio

import pytest

from travel_agent.application.errors import ApplicationForbiddenError
from travel_agent.application.errors import (
    ApplicationConflictError,
    ApplicationNotFoundError,
)
from travel_agent.application.service import TravelApplicationService
from travel_agent.config import Settings
from travel_agent.domain.models import PlanningRequest
from travel_agent.execution.models import RunStatus, TraceEventType
from travel_agent.identity.models import Principal
from travel_agent.runtime import PlanningRuntime


@pytest.mark.asyncio
async def test_async_run_is_reserved_before_execution_and_persists_trace(hangzhou_trip):
    runtime = await PlanningRuntime.create(Settings())
    service = TravelApplicationService(runtime)
    owner = Principal(tenant_id="tenant-a", user_id="user-a")
    try:
        trip = await service.create_trip(
            PlanningRequest(trip=hangzhou_trip), principal=owner
        )
        handle = await service.start_trip_run(trip.trip_id, principal=owner)
        first = await service.get_run(handle.run_id, principal=owner)
        assert first.status is RunStatus.RUNNING

        for _ in range(200):
            result = await service.get_run(handle.run_id, principal=owner)
            if result.status is not RunStatus.RUNNING:
                break
            await asyncio.sleep(0.01)
        assert result.status in {RunStatus.COMPLETED, RunStatus.INTERRUPTED}
        trace = await service.get_trace(handle.run_id, principal=owner, limit=500)
        assert trace[0].event_type.value == "run.started"
        assert trace[-1].event_type.value in {"run.completed", "run.interrupted"}
    finally:
        await service.close()
        await runtime.close()


@pytest.mark.asyncio
async def test_application_service_enforces_trip_owner(hangzhou_trip):
    runtime = await PlanningRuntime.create(Settings())
    service = TravelApplicationService(runtime)
    owner = Principal(tenant_id="tenant-a", user_id="user-a")
    outsider = Principal(tenant_id="tenant-a", user_id="user-b")
    try:
        trip = await service.create_trip(
            PlanningRequest(trip=hangzhou_trip), principal=owner
        )
        with pytest.raises(ApplicationForbiddenError):
            await service.get_trip(trip.trip_id, principal=outsider)
    finally:
        await service.close()
        await runtime.close()


class RecordingRunQueue:
    def __init__(self, *, removable: bool) -> None:
        self.removable = removable
        self.jobs = []

    async def enqueue(self, job) -> None:
        self.jobs.append(job)

    async def remove(self, _run_id: str) -> bool:
        return self.removable


@pytest.mark.asyncio
async def test_queued_run_can_be_cancelled_before_worker_claims_it(hangzhou_trip):
    runtime = await PlanningRuntime.create(Settings())
    queue = RecordingRunQueue(removable=True)
    service = TravelApplicationService(runtime, run_queue=queue)
    owner = Principal(tenant_id="tenant-a", user_id="user-a")
    try:
        with pytest.raises(ApplicationNotFoundError):
            await service.get_trip("missing", principal=owner)
        trip = await service.create_trip(
            PlanningRequest(trip=hangzhou_trip), principal=owner
        )
        handle = await service.start_trip_run(
            trip.trip_id,
            principal=owner,
            request_id="queued-cancel-1",
        )
        assert queue.jobs[0].run_id == handle.run_id

        cancelled = await service.cancel_run(handle.run_id, principal=owner)
        assert cancelled.status is RunStatus.CANCELLED
        assert (await runtime.get_agent_run(handle.run_id)).status is RunStatus.CANCELLED
        trace = await runtime.get_agent_trace(handle.run_id)
        assert trace[-1].event_type is TraceEventType.RUN_CANCELLED
    finally:
        await service.close()
        await runtime.close()


@pytest.mark.asyncio
async def test_claimed_remote_run_cannot_be_cancelled_by_api_process(hangzhou_trip):
    runtime = await PlanningRuntime.create(Settings())
    queue = RecordingRunQueue(removable=False)
    service = TravelApplicationService(runtime, run_queue=queue)
    owner = Principal(tenant_id="tenant-a", user_id="user-a")
    try:
        trip = await service.create_trip(
            PlanningRequest(trip=hangzhou_trip), principal=owner
        )
        handle = await service.start_trip_run(trip.trip_id, principal=owner)
        with pytest.raises(ApplicationConflictError):
            await service.cancel_run(handle.run_id, principal=owner)
    finally:
        await service.close()
        await runtime.close()
