from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
import sqlite3
from typing import Protocol

from travel_agent.domain.lifecycle_models import PlanSessionRecord, utcnow
from travel_agent.lifecycle.errors import LifecycleConflictError, LifecycleNotFoundError
from travel_agent.execution.context import match_fault, record_repository
from travel_agent.execution.faults import FaultMode, FaultPoint


class PlanRepository(Protocol):
    async def create(self, session: PlanSessionRecord) -> None: ...
    async def get(self, session_id: str) -> PlanSessionRecord: ...
    async def save(
        self, session: PlanSessionRecord, *, expected_revision: int
    ) -> None: ...
    async def close(self) -> None: ...


class InMemoryPlanRepository:
    def __init__(self) -> None:
        self._sessions: dict[str, PlanSessionRecord] = {}
        self._lock = asyncio.Lock()

    async def create(self, session: PlanSessionRecord) -> None:
        _inject_repository_fault("create")
        async with self._lock:
            if session.session_id in self._sessions:
                raise LifecycleConflictError(session.session_id)
            self._sessions[session.session_id] = session.model_copy(deep=True)
        record_repository("create", status="success")

    async def get(self, session_id: str) -> PlanSessionRecord:
        _inject_repository_fault("get")
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise LifecycleNotFoundError(session_id)
            result = session.model_copy(deep=True)
        record_repository("get", status="success")
        return result

    async def save(
        self, session: PlanSessionRecord, *, expected_revision: int
    ) -> None:
        _inject_repository_fault("save")
        async with self._lock:
            current = self._sessions.get(session.session_id)
            if current is None:
                raise LifecycleNotFoundError(session.session_id)
            if current.session_revision != expected_revision:
                raise LifecycleConflictError(
                    session.session_id,
                    code="stale_revision",
                )
            session.updated_at = utcnow()
            self._sessions[session.session_id] = session.model_copy(deep=True)
        record_repository("save", status="success")

    async def close(self) -> None:
        return None


class SQLitePlanRepository:
    """单进程 SQLite Repository；CAS 在事务中完成。"""

    def __init__(self, path: str) -> None:
        database_path = Path(path).expanduser()
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS plan_sessions ("
            "session_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL)"
        )
        self._connection.commit()
        self._lock = asyncio.Lock()

    async def create(self, session: PlanSessionRecord) -> None:
        _inject_repository_fault("create")
        async with self._lock:
            try:
                self._connection.execute(
                    "INSERT INTO plan_sessions(session_id, revision, payload) VALUES (?, ?, ?)",
                    (session.session_id, session.session_revision, session.model_dump_json()),
                )
                self._connection.commit()
            except sqlite3.IntegrityError:
                raise LifecycleConflictError(session.session_id) from None
        record_repository("create", status="success")

    async def get(self, session_id: str) -> PlanSessionRecord:
        _inject_repository_fault("get")
        async with self._lock:
            row = self._connection.execute(
                "SELECT payload FROM plan_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        if row is None:
            raise LifecycleNotFoundError(session_id)
        result = PlanSessionRecord.model_validate_json(row[0])
        record_repository("get", status="success")
        return result

    async def save(
        self, session: PlanSessionRecord, *, expected_revision: int
    ) -> None:
        _inject_repository_fault("save")
        async with self._lock:
            session.updated_at = utcnow()
            cursor = self._connection.execute(
                "UPDATE plan_sessions SET revision = ?, payload = ? "
                "WHERE session_id = ? AND revision = ?",
                (
                    session.session_revision,
                    session.model_dump_json(),
                    session.session_id,
                    expected_revision,
                ),
            )
            self._connection.commit()
            if cursor.rowcount != 1:
                exists = self._connection.execute(
                    "SELECT 1 FROM plan_sessions WHERE session_id = ?",
                    (session.session_id,),
                ).fetchone()
                if exists is None:
                    raise LifecycleNotFoundError(session.session_id)
                record_repository(
                    "cas_conflict", status="conflict", code="stale_revision"
                )
                raise LifecycleConflictError(session.session_id, code="stale_revision")
        record_repository("save", status="success")

    async def close(self) -> None:
        async with self._lock:
            self._connection.close()


@asynccontextmanager
async def open_plan_repository(
    *, backend: str, sqlite_path: str
) -> AsyncIterator[PlanRepository]:
    repository: PlanRepository
    if backend == "sqlite":
        repository = SQLitePlanRepository(sqlite_path)
    else:
        repository = InMemoryPlanRepository()
    try:
        yield repository
    finally:
        await repository.close()


class PlanRepositoryInjectedError(RuntimeError):
    pass


def _inject_repository_fault(operation: str) -> None:
    mode = match_fault(
        FaultPoint.PLAN_REPOSITORY, operation=operation, attempt=1
    )
    if mode in {
        FaultMode.WRITE_FAILURE,
        FaultMode.CONNECTION_ERROR,
        FaultMode.TIMEOUT,
    }:
        raise PlanRepositoryInjectedError(
            f"injected plan repository failure: {mode.value}"
        )
