from __future__ import annotations

import asyncio
from datetime import date

import pytest

from travel_agent.config import Settings
from travel_agent.requirements.errors import ClarificationResumeConflictError
from travel_agent.requirements.models import (
    ClarificationResumeRequest,
    NaturalPlanningRequest,
)
from travel_agent.runtime import PlanningRuntime


@pytest.mark.asyncio
async def test_sqlite_checkpoint_resumes_after_runtime_recreation(tmp_path):
    pytest.importorskip("langgraph.checkpoint.sqlite.aio")
    settings = Settings.from_env(
        {
            "CHECKPOINT_BACKEND": "sqlite",
            "CHECKPOINT_SQLITE_PATH": str(tmp_path / "requirements.sqlite3"),
        }
    )
    thread_id = "sqlite-resume"
    first_runtime = await PlanningRuntime.create(settings)
    try:
        interrupted = await first_runtime.plan_from_text(
            NaturalPlanningRequest(
                text=(
                    "2026年10月2日到10月4日去杭州，3个人，预算1500元，"
                    "住西湖东侧，喜欢自然和美食，2日10:30到杭州东站，"
                    "灵隐寺必须去，不想太累。"
                ),
                reference_date=date(2026, 8, 23),
            ),
            thread_id=thread_id,
        )
    finally:
        await first_runtime.close()

    assert interrupted.interrupt is not None

    second_runtime = await PlanningRuntime.create(settings)
    try:
        completed = await second_runtime.resume_from_text(
            ClarificationResumeRequest(
                interrupt_id=interrupted.interrupt.id,
                request_id="13f96bb7-2fe0-4243-afc1-5d409a22799c",
                answer="10月4日19:00从杭州东站离开。",
            ),
            thread_id=thread_id,
        )
    finally:
        await second_runtime.close()

    assert completed.status == "completed"
    assert completed.trip is not None
    assert completed.trip.departure.name == "杭州东站"


@pytest.mark.asyncio
async def test_runtime_serializes_concurrent_resume_requests():
    runtime = await PlanningRuntime.create(Settings.from_env({}))
    thread_id = "concurrent-resume"
    try:
        interrupted = await runtime.plan_from_text(
            NaturalPlanningRequest(
                text=(
                    "2026年10月2日到10月4日去杭州，3个人，预算1500元，"
                    "住西湖东侧，喜欢自然和美食，2日10:30到杭州东站，"
                    "灵隐寺必须去，不想太累。"
                ),
                reference_date=date(2026, 8, 23),
            ),
            thread_id=thread_id,
        )
        assert interrupted.interrupt is not None
        requests = [
            ClarificationResumeRequest(
                interrupt_id=interrupted.interrupt.id,
                request_id=request_id,
                answer="10月4日19:00从杭州东站离开。",
            )
            for request_id in (
                "4640aed4-83d8-43fe-af7f-d86c09d7b496",
                "f89ba09e-a966-463b-b3e1-9c852fe7b93a",
            )
        ]

        results = await asyncio.gather(
            *(runtime.resume_from_text(item, thread_id) for item in requests),
            return_exceptions=True,
        )
    finally:
        await runtime.close()

    assert sum(
        getattr(result, "status", None) == "completed" for result in results
    ) == 1
    assert sum(
        isinstance(result, ClarificationResumeConflictError) for result in results
    ) == 1
