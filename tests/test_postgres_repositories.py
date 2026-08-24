from __future__ import annotations

from datetime import datetime, timezone

import pytest

from travel_agent.domain.lifecycle_models import PlanSessionRecord, PlanSessionStatus
from travel_agent.execution.errors import RunNotFoundError
from travel_agent.execution.models import (
    AgentRunRecord,
    ExecutionBudget,
    RunKind,
    RunStatus,
    TraceEvent,
    TraceEventType,
)
from travel_agent.infrastructure.postgres import (
    PostgresPlanRepository,
    PostgresPreferenceRepository,
    PostgresRunRepository,
    create_postgres_pool,
)
from travel_agent.lifecycle.errors import LifecycleConflictError, LifecycleNotFoundError
from travel_agent.memory.errors import MemoryConflictError, MemoryNotFoundError
from travel_agent.memory.models import (
    ConfirmationStatus,
    MemoryCategory,
    MemoryProposal,
    MemorySource,
    PersonalizationSettings,
    PreferenceMemory,
)


class ScriptedPool:
    def __init__(self) -> None:
        self.execute_results: list[object] = []
        self.fetchrow_results: list[object] = []
        self.fetch_results: list[object] = []
        self.fetchval_results: list[object] = []
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.executemany_calls: list[tuple[str, object]] = []
        self.closed = False

    async def execute(self, sql: str, *args):
        self.executed.append((sql, args))
        if self.execute_results:
            result = self.execute_results.pop(0)
            if isinstance(result, BaseException):
                raise result
            return result
        if sql.startswith("DELETE"):
            return "DELETE 1"
        if sql.startswith("INSERT"):
            return "INSERT 0 1"
        return "UPDATE 1"

    async def fetchrow(self, _sql: str, *_args):
        return self.fetchrow_results.pop(0) if self.fetchrow_results else None

    async def fetch(self, _sql: str, *_args):
        return self.fetch_results.pop(0) if self.fetch_results else []

    async def fetchval(self, _sql: str, *_args):
        return self.fetchval_results.pop(0) if self.fetchval_results else None

    async def executemany(self, sql: str, args) -> None:
        self.executemany_calls.append((sql, list(args)))

    def acquire(self):
        return self

    def transaction(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def close(self) -> None:
        self.closed = True


def row(model) -> dict[str, object]:
    return {"payload": model.model_dump(mode="json")}


def make_session(session_id: str = "session-1") -> PlanSessionRecord:
    return PlanSessionRecord(
        session_id=session_id,
        lifecycle_thread_id=f"lifecycle:{session_id}",
        status=PlanSessionStatus.AWAITING_CANDIDATE_SELECTION,
        tenant_id="tenant-a",
        user_id="user-a",
    )


def make_run(run_id: str = "run-1") -> AgentRunRecord:
    return AgentRunRecord(
        run_id=run_id,
        run_kind=RunKind.STRUCTURED_PLAN,
        status=RunStatus.RUNNING,
        thread_id="thread-1",
        session_id="session-1",
        request_id="request-1",
        budget=ExecutionBudget(),
        started_at=datetime.now(timezone.utc),
        config_fingerprint="config",
        tenant_id="tenant-a",
        user_id="user-a",
    )


def make_event(run_id: str = "run-1") -> TraceEvent:
    return TraceEvent(
        event_id="event-1",
        run_id=run_id,
        sequence=1,
        event_type=TraceEventType.RUN_STARTED,
        timestamp=datetime.now(timezone.utc),
        monotonic_offset_ms=0,
        status="running",
    )


def make_memory(memory_id: str = "memory-1") -> PreferenceMemory:
    return PreferenceMemory(
        memory_id=memory_id,
        tenant_id="tenant-a",
        user_id="user-a",
        category=MemoryCategory.PACE,
        value="relaxed",
        source=MemorySource.EXPLICIT_USER,
        confidence=1,
        confirmation_status=ConfirmationStatus.CONFIRMED,
        content_hash="a" * 32,
    )


def make_proposal() -> MemoryProposal:
    return MemoryProposal(
        proposal_id="proposal-1",
        tenant_id="tenant-a",
        user_id="user-a",
        category=MemoryCategory.PACE,
        value="relaxed",
        source=MemorySource.MODEL_INFERENCE,
        confidence=0.7,
        reason="repeated choice",
        content_hash="b" * 32,
    )


@pytest.mark.asyncio
async def test_postgres_plan_repository_success_and_cas_errors() -> None:
    session = make_session()
    pool = ScriptedPool()
    repository = PostgresPlanRepository(pool)
    await repository.create(session)
    pool.fetchrow_results.append(row(session))
    assert (await repository.get(session.session_id)).session_id == session.session_id
    await repository.save(session, expected_revision=0)

    pool.fetchrow_results.append(None)
    with pytest.raises(LifecycleNotFoundError):
        await repository.get("missing")

    pool.execute_results.append("UPDATE 0")
    pool.fetchval_results.append(1)
    with pytest.raises(LifecycleConflictError):
        await repository.save(session, expected_revision=99)
    pool.execute_results.append("UPDATE 0")
    pool.fetchval_results.append(None)
    with pytest.raises(LifecycleNotFoundError):
        await repository.save(make_session("missing"), expected_revision=0)
    await repository.close()
    assert pool.closed is True


@pytest.mark.asyncio
async def test_postgres_run_repository_persists_record_and_trace() -> None:
    run = make_run()
    event = make_event()
    pool = ScriptedPool()
    repository = PostgresRunRepository(pool)
    await repository.create(run)
    await repository.finalize(run, (event,))
    assert len(pool.executemany_calls) == 1

    pool.fetchrow_results.extend([row(run), row(run), row(run)])
    pool.fetch_results.extend([[row(event)], [row(run)], [row(run)]])
    assert (await repository.get(run.run_id)).run_id == run.run_id
    assert (await repository.trace(run.run_id))[0].event_id == event.event_id
    assert (await repository.list_for_session("session-1"))[0].run_id == run.run_id
    assert (await repository.list_for_thread("thread-1"))[0].run_id == run.run_id
    assert (
        await repository.find_request(
            request_id="request-1", session_id="session-1", thread_id=None
        )
    ).run_id == run.run_id

    pool.fetchrow_results.append(None)
    with pytest.raises(RunNotFoundError):
        await repository.get("missing")
    pool.execute_results.append("UPDATE 0")
    with pytest.raises(RunNotFoundError):
        await repository.finalize(run, ())
    with pytest.raises(ValueError):
        await repository._list("unsafe", "x", 1)
    await repository.close()


@pytest.mark.asyncio
async def test_postgres_preference_repository_crud_and_personalization() -> None:
    memory = make_memory()
    proposal = make_proposal()
    settings = PersonalizationSettings(tenant_id="tenant-a", user_id="user-a")
    pool = ScriptedPool()
    repository = PostgresPreferenceRepository(pool)

    pool.fetch_results.append([row(memory)])
    assert (await repository.list_memories("tenant-a", "user-a"))[0] == memory
    pool.fetchrow_results.extend(
        [row(memory), row(memory), row(proposal), row(settings)]
    )
    assert await repository.get_memory("tenant-a", "user-a", "memory-1") == memory
    await repository.create_memory(memory)
    await repository.save_memory(memory, expected_revision=1)
    await repository.delete_memory("tenant-a", "user-a", "memory-1")
    pool.execute_results.append("DELETE 3")
    assert await repository.clear_memories("tenant-a", "user-a") == 3
    assert await repository.find_content_hash("tenant-a", "user-a", "a" * 32) == memory
    await repository.create_proposal(proposal)
    assert await repository.get_proposal("tenant-a", "user-a", "proposal-1") == proposal
    await repository.save_proposal(proposal)
    assert await repository.get_personalization("tenant-a", "user-a") == settings

    await repository.save_personalization(settings, expected_revision=None)
    await repository.save_personalization(settings, expected_revision=0)
    await repository.save_personalization(settings, expected_revision=1)

    pool.fetchrow_results.extend([None, None])
    with pytest.raises(MemoryNotFoundError):
        await repository.get_memory("tenant-a", "user-a", "missing")
    with pytest.raises(MemoryNotFoundError):
        await repository.get_proposal("tenant-a", "user-a", "missing")
    pool.execute_results.append("UPDATE 0")
    pool.fetchval_results.append(1)
    with pytest.raises(MemoryConflictError):
        await repository.save_memory(memory, expected_revision=99)
    pool.execute_results.append("DELETE 0")
    with pytest.raises(MemoryNotFoundError):
        await repository.delete_memory("tenant-a", "user-a", "missing")
    pool.execute_results.append("UPDATE 0")
    with pytest.raises(MemoryConflictError):
        await repository.save_personalization(settings, expected_revision=9)

    empty_pool = ScriptedPool()
    empty_repository = PostgresPreferenceRepository(empty_pool)
    defaults = await empty_repository.get_personalization("tenant-a", "new-user")
    assert defaults.enabled is True
    await repository.close()
    assert pool.closed is True


@pytest.mark.asyncio
async def test_postgres_repositories_map_unique_violations_to_domain_conflicts() -> None:
    class UniqueViolationError(Exception):
        pass

    plan_pool = ScriptedPool()
    plan_pool.execute_results.append(UniqueViolationError())
    with pytest.raises(LifecycleConflictError):
        await PostgresPlanRepository(plan_pool).create(make_session())

    memory_pool = ScriptedPool()
    memory_pool.execute_results.append(UniqueViolationError())
    preference_repository = PostgresPreferenceRepository(memory_pool)
    with pytest.raises(MemoryConflictError):
        await preference_repository.create_memory(make_memory())

    memory_pool.execute_results.append(UniqueViolationError())
    with pytest.raises(MemoryConflictError):
        await preference_repository.create_proposal(make_proposal())


@pytest.mark.asyncio
async def test_postgres_pool_requires_production_extra() -> None:
    with pytest.raises(RuntimeError, match="production extra"):
        await create_postgres_pool("postgresql://unused")
