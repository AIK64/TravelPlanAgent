from __future__ import annotations

from datetime import date
import logging

import pytest

from travel_agent.requirements.errors import (
    RequirementErrorCategory,
    RequirementProviderError,
    RequirementUnavailableError,
)
from travel_agent.requirements.gateway import RequirementGateway
from travel_agent.requirements.models import (
    NaturalPlanningRequest,
    RequirementDraft,
    RequirementProviderOutput,
)


class TimeoutThenSuccessModel:
    name = "recording"
    model = "recording-v1"

    def __init__(self) -> None:
        self.calls = 0

    async def parse(self, _request):
        self.calls += 1
        if self.calls == 1:
            raise RequirementProviderError(
                category=RequirementErrorCategory.TIMEOUT,
                code="timeout",
                retryable=True,
                safe_message="需求解析服务暂时超时",
            )
        return RequirementProviderOutput(
            draft=RequirementDraft(destination="杭州"),
            input_tokens=10,
            output_tokens=5,
        )


class AlwaysUnavailableModel:
    name = "recording"
    model = "recording-v1"

    async def parse(self, _request):
        raise RequirementProviderError(
            category=RequirementErrorCategory.AUTHENTICATION,
            code="authentication",
            retryable=False,
            safe_message="需求解析服务配置无效",
        )


@pytest.mark.asyncio
async def test_requirement_gateway_retries_safely_and_records_summary(caplog):
    model = TimeoutThenSuccessModel()
    sleeps = []

    async def record_sleep(delay):
        sleeps.append(delay)

    gateway = RequirementGateway(
        model=model,
        timeout_seconds=1,
        max_attempts=2,
        base_delay_seconds=0.1,
        max_delay_seconds=1,
        sleeper=record_sleep,
    )
    caplog.set_level(logging.INFO, logger="travel_agent.requirements.gateway")

    result = await gateway.parse(
        NaturalPlanningRequest(
            text="private raw requirement",
            reference_date=date(2026, 8, 23),
        ),
        thread_id="requirement-retry",
    )

    assert model.calls == 2
    assert sleeps == [0.1]
    assert result.summary.attempt_count == 2
    assert result.summary.input_tokens == 10
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "requirement.parse.started" in messages
    assert "requirement.parse.retry_scheduled" in messages
    assert "requirement.parse.completed" in messages
    assert "private raw requirement" not in messages


@pytest.mark.asyncio
async def test_requirement_gateway_exposes_only_safe_failure_detail():
    gateway = RequirementGateway(
        model=AlwaysUnavailableModel(),
        timeout_seconds=1,
        max_attempts=3,
        base_delay_seconds=0,
        max_delay_seconds=0,
    )

    with pytest.raises(RequirementUnavailableError) as captured:
        await gateway.parse(
            NaturalPlanningRequest(
                text="secret user context",
                reference_date=date(2026, 8, 23),
            ),
            thread_id="requirement-failed",
        )

    assert captured.value.safe_detail() == {
        "code": "authentication",
        "provider": "recording",
        "model": "recording-v1",
        "category": "authentication",
        "retryable": False,
        "thread_id": "requirement-failed",
        "message": "需求解析服务配置无效",
    }

