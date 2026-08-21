from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from travel_agent.tools.errors import ToolProviderError, ToolRetryExhausted
from travel_agent.tools.retry import RetryPolicy, RetryEvent


async def record_sleep(sleeps: list[float], delay: float) -> None:
    sleeps.append(delay)


async def raise_async(error: Exception):
    raise error


@pytest.mark.asyncio
async def test_retryable_error_uses_backoff_then_succeeds():
    attempts = 0
    sleeps: list[float] = []

    async def call():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ToolProviderError.timeout("route")
        return "ok"

    policy = RetryPolicy(
        max_attempts=3,
        base_delay_seconds=0.25,
        max_delay_seconds=2,
        sleeper=lambda delay: record_sleep(sleeps, delay),
        jitter=lambda: 0.0,
    )

    outcome = await policy.execute(call)

    assert outcome.value == "ok"
    assert outcome.attempts == 3
    assert attempts == 3
    assert sleeps == [0.25, 0.5]


@pytest.mark.asyncio
async def test_non_retryable_error_stops_after_one_attempt():
    sleeper = AsyncMock()
    policy = RetryPolicy(max_attempts=3, sleeper=sleeper, jitter=lambda: 0.0)

    with pytest.raises(ToolRetryExhausted) as captured:
        await policy.execute(
            lambda: raise_async(ToolProviderError.authentication("poi"))
        )

    assert captured.value.attempts == 1
    assert captured.value.last_error.category == ToolProviderError.authentication(
        "poi"
    ).category
    sleeper.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_exhaustion_preserves_last_error_and_attempt_count():
    error = ToolProviderError.timeout("route")
    attempts = 0

    async def call():
        nonlocal attempts
        attempts += 1
        raise error

    policy = RetryPolicy(
        max_attempts=3,
        sleeper=AsyncMock(),
        jitter=lambda: 0.0,
    )

    with pytest.raises(ToolRetryExhausted) as captured:
        await policy.execute(call)

    assert captured.value.last_error is error
    assert captured.value.attempts == 3
    assert attempts == 3


@pytest.mark.asyncio
async def test_retry_after_delay_overrides_lower_exponential_delay():
    sleeps: list[float] = []
    attempts = 0

    async def call():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ToolProviderError(
                category=ToolProviderError.timeout("poi").category,
                code="rate_limit",
                operation="poi",
                retryable=True,
                safe_message="Try again later.",
                retry_after_seconds=1.5,
            )
        return "ok"

    policy = RetryPolicy(
        max_attempts=2,
        base_delay_seconds=0.25,
        max_delay_seconds=2,
        sleeper=lambda delay: record_sleep(sleeps, delay),
        jitter=lambda: 0.0,
    )

    outcome = await policy.execute(call)

    assert outcome.value == "ok"
    assert sleeps == [1.5]


@pytest.mark.asyncio
async def test_jittered_retry_delay_is_finally_clamped_to_maximum():
    """防止 jitter 在首次 clamp 后再次突破公开最大退避上界。"""
    sleeps: list[float] = []
    attempts = 0

    async def call():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ToolProviderError.timeout("poi")
        return "ok"

    policy = RetryPolicy(
        max_attempts=2,
        base_delay_seconds=2,
        max_delay_seconds=2,
        sleeper=lambda delay: record_sleep(sleeps, delay),
        jitter=lambda: 1.0,
    )

    outcome = await policy.execute(call)

    assert outcome.value == "ok"
    assert sleeps == [2]


@pytest.mark.asyncio
async def test_on_retry_receives_typed_event_before_sleep():
    sleeps: list[float] = []
    events: list[RetryEvent] = []
    error = ToolProviderError.timeout("route")
    attempts = 0

    async def call():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise error
        return "ok"

    async def on_retry(event: RetryEvent) -> None:
        events.append(event)

    policy = RetryPolicy(
        max_attempts=2,
        base_delay_seconds=0.25,
        max_delay_seconds=2,
        sleeper=lambda delay: record_sleep(sleeps, delay),
        jitter=lambda: 0.0,
    )

    await policy.execute(call, on_retry=on_retry)

    assert events == [RetryEvent(1, 2, 0.25, error)]
    assert sleeps == [0.25]
