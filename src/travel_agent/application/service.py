from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from uuid import uuid4

from travel_agent.application.errors import (
    ApplicationConflictError,
    ApplicationForbiddenError,
    ApplicationNotFoundError,
)
from travel_agent.application.models import RunHandle, TripRecord
from travel_agent.domain.models import PlanningRequest, PlanningResponse
from travel_agent.domain.lifecycle_models import (
    LifecycleResumeRequest,
    PlanDiff,
    PlanSessionResponse,
    PlanVersion,
)
from travel_agent.domain.weather_models import (
    WeatherEventView,
    WeatherRefreshRequest,
    WeatherStateView,
)
from travel_agent.execution.coordinator import ExecutionResult
from travel_agent.execution.errors import RunNotFoundError
from travel_agent.execution.models import (
    AgentRunRecord,
    RunStatus,
    RunTerminalReason,
    TraceEvent,
    TraceEventType,
)
from travel_agent.identity.models import Principal
from travel_agent.infrastructure.queue import RunJob, RunQueue
from travel_agent.runtime import PlanningRuntime


logger = logging.getLogger(__name__)


class TravelApplicationService:
    """REST、MCP 与 Worker 共用的用例入口；不复制 Graph 领域规则。"""

    def __init__(
        self, runtime: PlanningRuntime, *, run_queue: RunQueue | None = None
    ) -> None:
        self.runtime = runtime
        self.run_queue = run_queue
        self._trips: dict[str, TripRecord] = {}
        self._tasks: dict[str, asyncio.Task[object]] = {}
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        tasks = tuple(task for task in self._tasks.values() if not task.done())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def create_trip(
        self, request: PlanningRequest, *, principal: Principal
    ) -> TripRecord:
        record = TripRecord(
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            request=request,
        )
        async with self._lock:
            self._trips[record.trip_id] = record
        logger.info(
            "application.trip_created | trip_id=%s tenant_id=%s user_id_hash=%s",
            record.trip_id,
            principal.tenant_id,
            principal.safe_user_hash,
        )
        return record

    async def get_trip(self, trip_id: str, *, principal: Principal) -> TripRecord:
        async with self._lock:
            record = self._trips.get(trip_id)
        if record is None:
            raise ApplicationNotFoundError("trip not found", details={"trip_id": trip_id})
        self._assert_owner(record.tenant_id, record.user_id, principal)
        return record

    async def start_trip_run(
        self,
        trip_id: str,
        *,
        principal: Principal,
        request_id: str | None = None,
    ) -> RunHandle:
        trip = await self.get_trip(trip_id, principal=principal)
        run_id = str(uuid4())
        thread_id = str(uuid4())

        async def execute() -> PlanningResponse:
            result = await self.runtime.execute_plan(
                trip.request,
                thread_id=thread_id,
                run_id=run_id,
                principal=principal,
                precreated=True,
            )
            return result.payload
        await self.runtime.reserve_plan_run(
            run_id=run_id,
            thread_id=thread_id,
            principal=principal,
            request_id=request_id,
        )
        if self.run_queue is not None:
            await self.run_queue.enqueue(
                RunJob(
                    run_id=run_id,
                    trip_id=trip_id,
                    thread_id=thread_id,
                    request=trip.request,
                    principal=principal,
                    request_id=request_id,
                )
            )
        else:
            task = asyncio.create_task(execute(), name=f"travel-run:{run_id}")
            async with self._lock:
                self._tasks[run_id] = task
            task.add_done_callback(
                lambda completed: self._log_task_result(run_id, completed)
            )
        logger.info(
            "application.run_submitted | run_id=%s trip_id=%s request_id=%s",
            run_id,
            trip_id,
            request_id or "none",
        )
        return RunHandle(
            run_id=run_id,
            trip_id=trip_id,
            thread_id=thread_id,
            status_url=f"/api/v1/runs/{run_id}",
            events_url=f"/api/v1/runs/{run_id}/events",
            cancel_url=f"/api/v1/runs/{run_id}/cancel",
        )

    async def get_run(
        self, run_id: str, *, principal: Principal
    ) -> AgentRunRecord:
        try:
            record = await self.runtime.get_agent_run(run_id)
        except RunNotFoundError:
            async with self._lock:
                pending = run_id in self._tasks
            if pending:
                await asyncio.sleep(0)
                record = await self.runtime.get_agent_run(run_id)
            else:
                raise RunNotFoundError(run_id) from None
        self._assert_owner(record.tenant_id, record.user_id, principal)
        return record

    async def get_trace(
        self,
        run_id: str,
        *,
        principal: Principal,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> tuple[TraceEvent, ...]:
        await self.get_run(run_id, principal=principal)
        return await self.runtime.get_agent_trace(
            run_id, after_sequence=after_sequence, limit=limit
        )

    async def cancel_run(
        self, run_id: str, *, principal: Principal
    ) -> AgentRunRecord:
        record = await self.get_run(run_id, principal=principal)
        if record.status.value != "running":
            return record
        async with self._lock:
            task = self._tasks.get(run_id)
        if task is None:
            if self.run_queue is not None and await self.run_queue.remove(run_id):
                ended = datetime.now(timezone.utc)
                cancelled = record.model_copy(
                    update={
                        "status": RunStatus.CANCELLED,
                        "terminal_reason": RunTerminalReason.USER_CANCELLED,
                        "ended_at": ended,
                        "elapsed_ms": max(
                            0,
                            round((ended - record.started_at).total_seconds() * 1000),
                        ),
                    }
                )
                assert self.runtime.run_repository is not None
                cancelled_event = TraceEvent(
                    event_id=str(uuid4()),
                    run_id=run_id,
                    sequence=1,
                    event_type=TraceEventType.RUN_CANCELLED,
                    timestamp=ended,
                    monotonic_offset_ms=cancelled.elapsed_ms or 0,
                    status=RunStatus.CANCELLED.value,
                    attributes={
                        "terminal_reason": RunTerminalReason.USER_CANCELLED.value
                    },
                )
                await self.runtime.run_repository.finalize(
                    cancelled, (cancelled_event,)
                )
                return cancelled
            raise ApplicationConflictError(
                "run is owned by another worker and cannot be cancelled locally",
                details={"run_id": run_id},
            )
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        logger.info("application.run_cancelled | run_id=%s", run_id)
        return await self.get_run(run_id, principal=principal)

    async def get_plan_session(
        self, session_id: str, *, principal: Principal
    ) -> PlanSessionResponse:
        await self._assert_session_owner(session_id, principal)
        return await self.runtime.get_plan_session(session_id=session_id)

    async def resume_plan_session(
        self,
        session_id: str,
        request: LifecycleResumeRequest,
        *,
        principal: Principal,
    ) -> PlanSessionResponse:
        return (
            await self.execute_resume_plan_session(
                session_id, request, principal=principal
            )
        ).payload

    async def execute_resume_plan_session(
        self,
        session_id: str,
        request: LifecycleResumeRequest,
        *,
        principal: Principal,
    ) -> ExecutionResult[PlanSessionResponse]:
        await self._assert_session_owner(session_id, principal)
        return await self.runtime.execute_resume_plan_session(
            request, session_id=session_id, principal=principal
        )

    async def get_plan_versions(
        self, session_id: str, *, principal: Principal
    ) -> tuple[PlanVersion, ...]:
        await self._assert_session_owner(session_id, principal)
        return await self.runtime.get_plan_versions(session_id=session_id)

    async def get_plan_version(
        self, session_id: str, version_id: str, *, principal: Principal
    ) -> PlanVersion:
        await self._assert_session_owner(session_id, principal)
        return await self.runtime.get_plan_version(
            session_id=session_id, version_id=version_id
        )

    async def get_plan_diff(
        self,
        session_id: str,
        from_version_id: str,
        to_version_id: str,
        *,
        principal: Principal,
    ) -> PlanDiff:
        await self._assert_session_owner(session_id, principal)
        return await self.runtime.get_plan_diff(
            session_id=session_id,
            from_id=from_version_id,
            to_id=to_version_id,
        )

    async def refresh_plan_weather(
        self,
        session_id: str,
        request: WeatherRefreshRequest,
        *,
        principal: Principal,
    ) -> ExecutionResult[PlanSessionResponse]:
        await self._assert_session_owner(session_id, principal)
        return await self.runtime.execute_refresh_plan_weather(
            request, session_id=session_id, principal=principal
        )

    async def get_plan_weather(
        self, session_id: str, *, principal: Principal
    ) -> WeatherStateView:
        await self._assert_session_owner(session_id, principal)
        return await self.runtime.get_plan_weather(session_id=session_id)

    async def get_plan_weather_events(
        self, session_id: str, *, principal: Principal
    ) -> tuple[WeatherEventView, ...]:
        await self._assert_session_owner(session_id, principal)
        return await self.runtime.get_plan_weather_events(session_id=session_id)

    async def get_plan_weather_event(
        self, session_id: str, event_id: str, *, principal: Principal
    ) -> WeatherEventView:
        await self._assert_session_owner(session_id, principal)
        return await self.runtime.get_plan_weather_event(
            session_id=session_id, event_id=event_id
        )

    async def get_session_runs(
        self, session_id: str, *, principal: Principal, limit: int = 50
    ) -> tuple[AgentRunRecord, ...]:
        await self._assert_session_owner(session_id, principal)
        records = await self.runtime.get_session_runs(session_id, limit=limit)
        return tuple(
            record
            for record in records
            if record.tenant_id == principal.tenant_id
            and record.user_id == principal.user_id
        )

    async def get_thread_runs(
        self, thread_id: str, *, principal: Principal, limit: int = 50
    ) -> tuple[AgentRunRecord, ...]:
        records = await self.runtime.get_thread_runs(thread_id, limit=500)
        owned = (
            record
            for record in records
            if record.tenant_id == principal.tenant_id
            and record.user_id == principal.user_id
        )
        return tuple(owned)[:limit]

    async def _assert_session_owner(
        self, session_id: str, principal: Principal
    ) -> None:
        repository = self.runtime.plan_repository
        if repository is None:
            raise RuntimeError("plan repository is not configured")
        record = await repository.get(session_id)
        self._assert_owner(record.tenant_id, record.user_id, principal)

    @staticmethod
    def _assert_owner(
        tenant_id: str, user_id: str, principal: Principal
    ) -> None:
        if tenant_id != principal.tenant_id or user_id != principal.user_id:
            raise ApplicationForbiddenError("resource belongs to another principal")

    @staticmethod
    def _log_task_result(run_id: str, task: asyncio.Task[object]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error(
                "application.run_task_failed | run_id=%s error_type=%s",
                run_id,
                type(error).__name__,
            )
