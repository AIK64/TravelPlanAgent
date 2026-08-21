"""供应商无关的、有界工具重试原语。"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Awaitable, Callable, Generic, TypeVar

from travel_agent.tools.errors import ToolProviderError, ToolRetryExhausted


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class RetryOutcome(Generic[T]):
    value: T
    attempts: int


@dataclass(frozen=True, slots=True)
class RetryEvent:
    attempt: int
    next_attempt: int
    delay_seconds: float
    error: ToolProviderError


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 0.25
    max_delay_seconds: float = 2.0
    sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep
    jitter: Callable[[], float] = random.random

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.base_delay_seconds < 0:
            raise ValueError("base_delay_seconds must be non-negative")
        if self.max_delay_seconds < 0:
            raise ValueError("max_delay_seconds must be non-negative")

    async def execute(
        self,
        call: Callable[[], Awaitable[T]],
        on_retry: Callable[[RetryEvent], Awaitable[None]] | None = None,
    ) -> RetryOutcome[T]:
        for attempt in range(1, self.max_attempts + 1):
            try:
                return RetryOutcome(value=await call(), attempts=attempt)
            except ToolProviderError as error:
                if not error.retryable or attempt == self.max_attempts:
                    raise ToolRetryExhausted(error, attempt) from error

                exponential = self.base_delay_seconds * (2 ** (attempt - 1))
                jittered = exponential + (
                    min(1.0, max(0.0, self.jitter())) * exponential
                )
                requested = error.retry_after_seconds or 0.0
                delay = min(
                    self.max_delay_seconds,
                    max(jittered, requested),
                )

                if on_retry is not None:
                    await on_retry(
                        RetryEvent(
                            attempt=attempt,
                            next_attempt=attempt + 1,
                            delay_seconds=delay,
                            error=error,
                        )
                    )
                await self.sleeper(delay)

        raise RuntimeError("retry loop exited unexpectedly")
