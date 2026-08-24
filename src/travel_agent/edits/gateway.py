from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import logging
from time import perf_counter

from travel_agent.domain.lifecycle_models import (
    EditExecutionSummary,
    EditModelInput,
    EditPatch,
)
from travel_agent.edits.errors import EditErrorCategory, EditProviderError, EditUnavailableError
from travel_agent.edits.protocols import EditModel


logger = logging.getLogger(__name__)


class EditGateway:
    def __init__(
        self,
        *,
        model: EditModel,
        timeout_seconds: float,
        max_attempts: int,
        base_delay_seconds: float = 0.5,
        max_delay_seconds: float = 2.0,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if timeout_seconds <= 0 or max_attempts < 1:
            raise ValueError("invalid edit gateway budget")
        self._model = model
        self._timeout = timeout_seconds
        self._max_attempts = max_attempts
        self._base_delay = base_delay_seconds
        self._max_delay = max_delay_seconds
        self._sleeper = sleeper

    async def parse(
        self, request: EditModelInput, *, session_id: str
    ) -> tuple[EditPatch, EditExecutionSummary]:
        started = perf_counter()
        logger.info(
            "edit.parse.started | session_id=%s provider=%s model=%s input_chars=%s",
            session_id,
            self._model.name,
            self._model.model,
            len(request.text),
        )
        last_error: EditProviderError | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                output = await asyncio.wait_for(
                    self._model.parse(request), timeout=self._timeout
                )
            except TimeoutError:
                last_error = EditProviderError(
                    EditErrorCategory.TIMEOUT,
                    "timeout",
                    True,
                    "计划编辑解析服务暂时超时",
                )
            except EditProviderError as error:
                last_error = error
            else:
                elapsed = round((perf_counter() - started) * 1000, 2)
                summary = EditExecutionSummary(
                    provider=self._model.name,
                    model=self._model.model,
                    prompt_version=self._model.prompt_version,
                    attempt_count=attempt,
                    elapsed_ms=elapsed,
                    input_tokens=output.input_tokens,
                    output_tokens=output.output_tokens,
                )
                logger.info(
                    "edit.parse.completed | session_id=%s provider=%s model=%s "
                    "attempt_count=%s elapsed_ms=%s operation_count=%s",
                    session_id,
                    self._model.name,
                    self._model.model,
                    attempt,
                    elapsed,
                    len(output.patch.operations),
                )
                return output.patch, summary
            assert last_error is not None
            if not last_error.retryable or attempt == self._max_attempts:
                raise EditUnavailableError(
                    provider=self._model.name,
                    model=self._model.model,
                    category=last_error.category,
                    code=last_error.code,
                    retryable=last_error.retryable,
                    safe_message=last_error.safe_message,
                    session_id=session_id,
                    attempt_count=attempt,
                ) from last_error
            await self._sleeper(
                min(self._max_delay, self._base_delay * (2 ** (attempt - 1)))
            )
        raise RuntimeError("edit retry loop exited unexpectedly")

