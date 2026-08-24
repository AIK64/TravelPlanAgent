import pytest

from travel_agent.domain.lifecycle_models import PlanSessionRecord, PlanSessionStatus
from travel_agent.lifecycle.errors import LifecycleConflictError, LifecycleNotFoundError
from travel_agent.lifecycle.repository import (
    InMemoryPlanRepository,
    SQLitePlanRepository,
    open_plan_repository,
)


@pytest.mark.asyncio
async def test_in_memory_repository_uses_revision_cas():
    repository = InMemoryPlanRepository()
    session = PlanSessionRecord(
        session_id="session",
        lifecycle_thread_id="lifecycle:session",
        status=PlanSessionStatus.AWAITING_CANDIDATE_SELECTION,
    )
    await repository.create(session)
    first = await repository.get("session")
    stale = await repository.get("session")
    first.session_revision = 1
    await repository.save(first, expected_revision=0)
    stale.session_revision = 1
    with pytest.raises(LifecycleConflictError):
        await repository.save(stale, expected_revision=0)


@pytest.mark.asyncio
async def test_sqlite_repository_survives_reopen(tmp_path):
    path = str(tmp_path / "plans.sqlite3")
    first = SQLitePlanRepository(path)
    await first.create(
        PlanSessionRecord(
            session_id="persisted",
            lifecycle_thread_id="lifecycle:persisted",
            status=PlanSessionStatus.AWAITING_CANDIDATE_SELECTION,
        )
    )
    await first.close()

    second = SQLitePlanRepository(path)
    restored = await second.get("persisted")
    await second.close()

    assert restored.session_id == "persisted"
    assert restored.status is PlanSessionStatus.AWAITING_CANDIDATE_SELECTION


@pytest.mark.asyncio
async def test_repository_not_found_and_duplicate_semantics(tmp_path):
    memory = InMemoryPlanRepository()
    with pytest.raises(LifecycleNotFoundError):
        await memory.get("missing")
    with pytest.raises(LifecycleNotFoundError):
        await memory.save(
            PlanSessionRecord(
                session_id="missing",
                lifecycle_thread_id="lifecycle:missing",
                status=PlanSessionStatus.AWAITING_CANDIDATE_SELECTION,
            ),
            expected_revision=0,
        )
    session = PlanSessionRecord(
        session_id="duplicate",
        lifecycle_thread_id="lifecycle:duplicate",
        status=PlanSessionStatus.AWAITING_CANDIDATE_SELECTION,
    )
    await memory.create(session)
    with pytest.raises(LifecycleConflictError):
        await memory.create(session)

    sqlite = SQLitePlanRepository(str(tmp_path / "duplicate.sqlite3"))
    await sqlite.create(session)
    with pytest.raises(LifecycleConflictError):
        await sqlite.create(session)
    with pytest.raises(LifecycleNotFoundError):
        await sqlite.get("missing")
    await sqlite.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_repository_context_manager_selects_backend(tmp_path, backend):
    async with open_plan_repository(
        backend=backend, sqlite_path=str(tmp_path / f"{backend}.sqlite3")
    ) as repository:
        await repository.create(
            PlanSessionRecord(
                session_id=backend,
                lifecycle_thread_id=f"lifecycle:{backend}",
                status=PlanSessionStatus.AWAITING_CANDIDATE_SELECTION,
            )
        )
        assert (await repository.get(backend)).session_id == backend
