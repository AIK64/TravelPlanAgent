from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import logging
from time import perf_counter

from travel_agent.critique.errors import (
    CriticErrorCategory,
    CriticProviderError,
    CriticUnavailableError,
)
from travel_agent.critique.protocols import CriticModel
from travel_agent.domain.critique_models import (
    CriticExecutionSummary,
    CriticStatus,
    SoftCriticRequest,
    SoftCriticResult,
)
from travel_agent.execution.context import (
    begin_llm,
    begin_llm_attempt,
    effective_timeout,
    finish_llm,
    llm_retry,
    match_fault,
)
from travel_agent.execution.faults import FaultMode, FaultPoint


logger = logging.getLogger(__name__)


class CriticGateway:
    """Soft Critic 的传输超时、有限重试和安全日志边界。"""

    def __init__(
        self,
        *,
        model: CriticModel,
        timeout_seconds: float,
        max_attempts: int,
        base_delay_seconds: float = 0.5,
        max_delay_seconds: float = 2.0,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if base_delay_seconds < 0 or max_delay_seconds < 0:
            raise ValueError("retry delays must be non-negative")
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max_attempts
        self._base_delay_seconds = base_delay_seconds
        self._max_delay_seconds = max_delay_seconds
        self._sleeper = sleeper

    @property
    def provider(self) -> str:
        return self._model.name

    @property
    def model(self) -> str:
        return self._model.model

    @property
    def prompt_version(self) -> str:
        return self._model.prompt_version

    async def critique(
        self,
        request: SoftCriticRequest,
        *,
        thread_id: str,
        grounding_attempt: int,
    ) -> SoftCriticResult:
        started = perf_counter()
        parent_event_id = begin_llm(
            "soft_critic",
            provider=self.provider,
            model=self.model,
            prompt_version=self.prompt_version,
            input_chars=request.input_chars,
        )
        logger.info(
            "critic.started | thread_id=%s provider=%s model=%s prompt_version=%s "
            "candidate_count=%s input_chars=%s grounding_attempt=%s",
            thread_id,
            self.provider,
            self.model,
            self.prompt_version,
            len(request.digests),
            request.input_chars,
            grounding_attempt,
        )
        last_error: CriticProviderError | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                begin_llm_attempt(
                    "soft_critic",
                    provider=self.provider,
                    model=self.model,
                    attempt=attempt,
                    parent_event_id=parent_event_id,
                )
                injected = match_fault(
                    FaultPoint.CRITIC_LLM,
                    operation="soft_critic",
                    attempt=attempt,
                )
                if injected is not None:
                    raise _injected_critic_error(injected)
                output = await asyncio.wait_for(
                    self._model.critique(request),
                    timeout=effective_timeout(self._timeout_seconds),
                )
            except TimeoutError:
                last_error = CriticProviderError(
                    CriticErrorCategory.TIMEOUT,
                    "timeout",
                    True,
                    "软质量评审服务暂时超时",
                )
            except CriticProviderError as error:
                last_error = error
            else:
                elapsed_ms = round((perf_counter() - started) * 1000, 2)
                summary = CriticExecutionSummary(
                    provider=self.provider,
                    model=self.model,
                    prompt_version=self.prompt_version,
                    status=CriticStatus.SUCCESS,
                    attempt_count=attempt,
                    grounding_attempt_count=grounding_attempt,
                    elapsed_ms=elapsed_ms,
                    input_tokens=output.input_tokens,
                    output_tokens=output.output_tokens,
                    input_chars=request.input_chars,
                )
                logger.info(
                    "critic.completed | thread_id=%s provider=%s model=%s "
                    "attempt_count=%s grounding_attempt=%s elapsed_ms=%s "
                    "input_tokens=%s output_tokens=%s critique_count=%s",
                    thread_id,
                    self.provider,
                    self.model,
                    attempt,
                    grounding_attempt,
                    elapsed_ms,
                    output.input_tokens,
                    output.output_tokens,
                    len(output.critiques),
                )
                finish_llm(
                    "soft_critic",
                    provider=self.provider,
                    model=self.model,
                    status="success",
                    attempt_count=attempt,
                    elapsed_ms=elapsed_ms,
                    input_tokens=output.input_tokens,
                    output_tokens=output.output_tokens,
                    error_code=None,
                    parent_event_id=parent_event_id,
                )
                return SoftCriticResult(critiques=output.critiques, summary=summary)

            assert last_error is not None
            if not last_error.retryable or attempt == self._max_attempts:
                elapsed_ms = round((perf_counter() - started) * 1000, 2)
                logger.warning(
                    "critic.failed | thread_id=%s provider=%s model=%s "
                    "attempt_count=%s grounding_attempt=%s category=%s code=%s",
                    thread_id,
                    self.provider,
                    self.model,
                    attempt,
                    grounding_attempt,
                    last_error.category.value,
                    last_error.code,
                )
                finish_llm(
                    "soft_critic",
                    provider=self.provider,
                    model=self.model,
                    status="failed",
                    attempt_count=attempt,
                    elapsed_ms=elapsed_ms,
                    input_tokens=None,
                    output_tokens=None,
                    error_code=last_error.code,
                    parent_event_id=parent_event_id,
                )
                raise CriticUnavailableError(
                    provider=self.provider,
                    model=self.model,
                    category=last_error.category,
                    code=last_error.code,
                    retryable=last_error.retryable,
                    safe_message=last_error.safe_message,
                    thread_id=thread_id,
                    attempt_count=attempt,
                    elapsed_ms=elapsed_ms,
                ) from last_error
            delay = min(
                self._max_delay_seconds,
                max(
                    self._base_delay_seconds * (2 ** (attempt - 1)),
                    last_error.retry_after_seconds or 0,
                ),
            )
            logger.info(
                "critic.retry_scheduled | thread_id=%s provider=%s model=%s "
                "attempt=%s next_attempt=%s delay_seconds=%s category=%s code=%s",
                thread_id,
                self.provider,
                self.model,
                attempt,
                attempt + 1,
                delay,
                last_error.category.value,
                last_error.code,
            )
            llm_retry(
                "soft_critic",
                attempt=attempt,
                category=last_error.category.value,
                code=last_error.code,
                parent_event_id=parent_event_id,
            )
            await self._sleeper(delay)
        raise RuntimeError("critic retry loop exited unexpectedly")


def _injected_critic_error(mode: FaultMode) -> CriticProviderError:
    category = {
        FaultMode.TIMEOUT: CriticErrorCategory.TIMEOUT,
        FaultMode.RATE_LIMIT: CriticErrorCategory.RATE_LIMIT,
        FaultMode.AUTH_ERROR: CriticErrorCategory.AUTHENTICATION,
        FaultMode.CONNECTION_ERROR: CriticErrorCategory.CONNECTION,
        FaultMode.INVALID_SCHEMA: CriticErrorCategory.INVALID_SCHEMA,
        FaultMode.EMPTY_BUSINESS_RESULT: CriticErrorCategory.INVALID_SCHEMA,
        FaultMode.WRITE_FAILURE: CriticErrorCategory.UPSTREAM_UNAVAILABLE,
    }[mode]
    return CriticProviderError(
        category,
        f"injected_{mode.value}",
        mode
        in {
            FaultMode.TIMEOUT,
            FaultMode.RATE_LIMIT,
            FaultMode.CONNECTION_ERROR,
            FaultMode.WRITE_FAILURE,
        },
        "Injected critic failure for evaluation",
    )
