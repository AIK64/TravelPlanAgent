from uuid import uuid4

import pytest

from travel_agent.config import Settings
from travel_agent.domain.lifecycle_models import LifecycleResumeRequest
from travel_agent.domain.models import PlanningRequest
from travel_agent.runtime import PlanningRuntime


@pytest.mark.asyncio
async def test_sqlite_runtime_restores_candidate_selection_interrupt(
    tmp_path, hangzhou_trip
):
    pytest.importorskip("aiosqlite")
    settings = Settings.from_env(
        {
            "CHECKPOINT_BACKEND": "sqlite",
            "CHECKPOINT_SQLITE_PATH": str(tmp_path / "checkpoints.sqlite3"),
            "PLAN_SQLITE_PATH": str(tmp_path / "plans.sqlite3"),
        }
    )
    first = await PlanningRuntime.create(settings)
    created = await first.create_plan_session(
        PlanningRequest(trip=hangzhou_trip), session_id="restart-session"
    )
    interrupt_id = created.interrupt.id
    await first.close()

    second = await PlanningRuntime.create(settings)
    try:
        restored = await second.get_plan_session(session_id="restart-session")
        assert restored.status.value == "awaiting_candidate_selection"
        assert restored.interrupt is not None
        assert restored.interrupt.id == interrupt_id

        resumed = await second.resume_plan_session(
            LifecycleResumeRequest(
                interrupt_id=restored.interrupt.id,
                request_id=uuid4(),
                expected_session_revision=0,
                action={"kind": "accept_recommendation"},
            ),
            session_id="restart-session",
        )
        assert resumed.active_version is not None
        assert resumed.active_version.version_id == "V1"
    finally:
        await second.close()

