from __future__ import annotations

from datetime import datetime, timezone

import pytest

from travel_agent.execution.models import (
    AgentRunRecord,
    ExecutionBudget,
    RunKind,
    RunStatus,
)
from travel_agent.execution.repository import SQLiteRunRepository


@pytest.mark.asyncio
async def test_sqlite_run_repository_survives_reopen(tmp_path):
    path = tmp_path / "runs.sqlite3"
    first = SQLiteRunRepository(str(path))
    run = AgentRunRecord(
        run_id="sqlite-run",
        run_kind=RunKind.STRUCTURED_PLAN,
        status=RunStatus.RUNNING,
        thread_id="thread-sqlite",
        budget=ExecutionBudget(),
        started_at=datetime.now(timezone.utc),
        config_fingerprint="config",
    )
    await first.create(run)
    completed = run.model_copy(update={"status": RunStatus.COMPLETED})
    await first.finalize(completed, ())
    await first.close()

    second = SQLiteRunRepository(str(path))
    restored = await second.get("sqlite-run")
    listed = await second.list_for_thread("thread-sqlite")
    await second.close()

    assert restored.status is RunStatus.COMPLETED
    assert listed[0].run_id == "sqlite-run"
