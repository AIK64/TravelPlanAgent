from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import logging
from time import perf_counter
from typing import Protocol, TypeVar

from travel_agent.requirements.errors import (
    RequirementErrorCategory,
    RequirementProviderError,
    RequirementUnavailableError,
)
from travel_agent.requirements.models import (
    ClarificationModelInput,
    NaturalPlanningRequest,
    RequirementExecutionSummary,
    RequirementModelResult,
    RequirementModelStatus,
    RequirementOperation,
    RequirementPatchModelResult,
)
from travel_agent.requirements.prompts import (
    CLARIFICATION_PROMPT_VERSION,
    REQUIREMENT_PROMPT_VERSION,
)
from travel_agent.requirements.protocols import RequirementModel


logger = logging.getLogger(__name__)
SafeScalar = str | int | float | bool | None


class _UsageOutput(Protocol):
    input_tokens: int | None
    output_tokens: int | None


OutputT = TypeVar("OutputT", bound=_UsageOutput)


def _log_event(event: str, **fields: SafeScalar) -> None:
    if not all(
        value is None or isinstance(value, (str, int, float, bool))
        for value in fields.values()
    ):
        raise TypeError("requirement log fields must be safe scalars")
    logger.info("%s %s", event, " ".join(f"{key}={value}" for key, value in fields.items()))


class RequirementGateway:
    """为一次结构化需求解析提供超时、有限重试和安全观测。"""

    def __init__(
        self,
        *,
        model: RequirementModel,
        timeout_seconds: float,
        max_attempts: int,
        base_delay_seconds: float,
        max_delay_seconds: float,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if base_delay_seconds < 0 or max_delay_seconds < 0:
            raise ValueError("retry delays must be non-negative")
        self._model = model
        self._prompt_version = getattr(
            model,
            "prompt_version",
            REQUIREMENT_PROMPT_VERSION,
        )
        self._clarification_prompt_version = getattr(
            model,
            "clarification_prompt_version",
            CLARIFICATION_PROMPT_VERSION,
        )
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max_attempts
        self._base_delay_seconds = base_delay_seconds
        self._max_delay_seconds = max_delay_seconds
        self._sleeper = sleeper

    async def parse(
        self,
        request: NaturalPlanningRequest,
        *,
        thread_id: str,
    ) -> RequirementModelResult:
        output, summary = await self._invoke(
            operation=RequirementOperation.INITIAL_PARSE,
            event_prefix="requirement.parse",
            prompt_version=self._prompt_version,
            input_chars=len(request.text),
            thread_id=thread_id,
            call=lambda: self._model.parse(request),
        )
        return RequirementModelResult(draft=output.draft, summary=summary)

    async def parse_clarification(
        self,
        request: ClarificationModelInput,
        *,
        thread_id: str,
    ) -> RequirementPatchModelResult:
        output, summary = await self._invoke(
            operation=RequirementOperation.CLARIFICATION_PATCH,
            event_prefix="clarification.patch",
            prompt_version=self._clarification_prompt_version,
            input_chars=len(request.answer),
            thread_id=thread_id,
            call=lambda: self._model.parse_clarification(request),
        )
        return RequirementPatchModelResult(patch=output.patch, summary=summary)

    async def _invoke(
        self,
        *,
        operation: RequirementOperation,
        event_prefix: str,
        prompt_version: str,
        input_chars: int,
        thread_id: str,
        call: Callable[[], Awaitable[OutputT]],
    ) -> tuple[OutputT, RequirementExecutionSummary]:
        started = perf_counter()
        _log_event(
            f"{event_prefix}.started",
            thread_id=thread_id,
            provider=self._model.name,
            model=self._model.model,
            prompt_version=prompt_version,
            operation=operation.value,
            input_chars=input_chars,
            attempt=1,
        )
        last_error: RequirementProviderError | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                output = await asyncio.wait_for(
                    call(),
                    timeout=self._timeout_seconds,
                )
            except TimeoutError:
                last_error = RequirementProviderError(
                    category=RequirementErrorCategory.TIMEOUT,
                    code="timeout",
                    retryable=True,
                    safe_message="需求解析服务暂时超时",
                )
            except RequirementProviderError as error:
                last_error = error
            else:
                elapsed_ms = round((perf_counter() - started) * 1000, 2)
                summary = RequirementExecutionSummary(
                    provider=self._model.name,
                    model=self._model.model,
                    prompt_version=prompt_version,
                    operation=operation,
                    status=RequirementModelStatus.SUCCESS,
                    attempt_count=attempt,
                    elapsed_ms=elapsed_ms,
                    input_tokens=output.input_tokens,
                    output_tokens=output.output_tokens,
                )
                _log_event(
                    f"{event_prefix}.completed",
                    thread_id=thread_id,
                    provider=self._model.name,
                    model=self._model.model,
                    prompt_version=prompt_version,
                    operation=operation.value,
                    attempt_count=attempt,
                    elapsed_ms=elapsed_ms,
                    input_tokens=output.input_tokens,
                    output_tokens=output.output_tokens,
                )
                return output, summary

            assert last_error is not None
            if not last_error.retryable or attempt == self._max_attempts:
                _log_event(
                    f"{event_prefix}.failed",
                    thread_id=thread_id,
                    provider=self._model.name,
                    model=self._model.model,
                    operation=operation.value,
                    attempt_count=attempt,
                    category=last_error.category.value,
                    code=last_error.code,
                    retryable=last_error.retryable,
                )
                raise RequirementUnavailableError(
                    provider=self._model.name,
                    model=self._model.model,
                    category=last_error.category,
                    code=last_error.code,
                    retryable=last_error.retryable,
                    safe_message=last_error.safe_message,
                    thread_id=thread_id,
                    attempt_count=attempt,
                ) from last_error

            delay = min(
                self._max_delay_seconds,
                max(
                    self._base_delay_seconds * (2 ** (attempt - 1)),
                    last_error.retry_after_seconds or 0,
                ),
            )
            _log_event(
                f"{event_prefix}.retry_scheduled",
                thread_id=thread_id,
                provider=self._model.name,
                model=self._model.model,
                operation=operation.value,
                attempt=attempt,
                next_attempt=attempt + 1,
                delay_seconds=delay,
                category=last_error.category.value,
                code=last_error.code,
            )
            await self._sleeper(delay)

        raise RuntimeError("requirement retry loop exited unexpectedly")
