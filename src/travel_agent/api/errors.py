from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from travel_agent.application.errors import ApplicationError

from travel_agent.tools.errors import ToolUnavailableError
from travel_agent.requirements.errors import RequirementUnavailableError
from travel_agent.requirements.errors import (
    ClarificationResumeConflictError,
    ClarificationThreadNotFoundError,
)
from travel_agent.edits.errors import EditUnavailableError
from travel_agent.lifecycle.errors import (
    LifecycleActionError,
    LifecycleConflictError,
    LifecycleNotFoundError,
)
from travel_agent.weather.errors import WeatherUnavailableError
from travel_agent.execution.errors import ExecutionBudgetExceeded, RunNotFoundError
from travel_agent.memory.errors import (
    MemoryConflictError,
    MemoryForbiddenError,
    MemoryNotFoundError,
    MemoryPolicyError,
)


logger = logging.getLogger(__name__)


class UTF8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"


def _run_headers(error: BaseException) -> dict[str, str] | None:
    run_id = getattr(error, "agent_run_id", None) or getattr(error, "run_id", None)
    return {"X-Agent-Run-Id": str(run_id)} if run_id else None


async def tool_unavailable_exception_handler(
    _request: Request,
    error: ToolUnavailableError,
) -> UTF8JSONResponse:
    detail = error.safe_detail()
    logger.error(
        "api.tool_unavailable thread_id=%s provider=%s operation=%s "
        "category=%s code=%s retryable=%s",
        detail["thread_id"],
        detail["provider"],
        detail["operation"],
        detail["category"],
        detail["code"],
        detail["retryable"],
        exc_info=(type(error), error, error.__traceback__),
    )
    return UTF8JSONResponse(
        status_code=503,
        content={"detail": detail},
        headers=_run_headers(error),
    )


async def requirement_unavailable_exception_handler(
    _request: Request,
    error: RequirementUnavailableError,
) -> UTF8JSONResponse:
    detail = error.safe_detail()
    logger.error(
        "api.requirement_unavailable thread_id=%s provider=%s model=%s "
        "category=%s code=%s retryable=%s",
        detail["thread_id"],
        detail["provider"],
        detail["model"],
        detail["category"],
        detail["code"],
        detail["retryable"],
        exc_info=(type(error), error, error.__traceback__),
    )
    return UTF8JSONResponse(
        status_code=503, content={"detail": detail}, headers=_run_headers(error)
    )


async def clarification_not_found_exception_handler(
    _request: Request,
    error: ClarificationThreadNotFoundError,
) -> UTF8JSONResponse:
    detail = error.safe_detail()
    logger.warning(
        "api.clarification_not_found thread_id=%s code=%s",
        detail["thread_id"],
        detail["code"],
    )
    return UTF8JSONResponse(
        status_code=404, content={"detail": detail}, headers=_run_headers(error)
    )


async def clarification_conflict_exception_handler(
    _request: Request,
    error: ClarificationResumeConflictError,
) -> UTF8JSONResponse:
    detail = error.safe_detail()
    logger.warning(
        "api.clarification_conflict thread_id=%s code=%s",
        detail["thread_id"],
        detail["code"],
    )
    return UTF8JSONResponse(
        status_code=409, content={"detail": detail}, headers=_run_headers(error)
    )


async def edit_unavailable_exception_handler(
    _request: Request, error: EditUnavailableError
) -> UTF8JSONResponse:
    detail = error.safe_detail()
    logger.error(
        "api.edit_unavailable session_id=%s provider=%s model=%s code=%s",
        detail["session_id"],
        detail["provider"],
        detail["model"],
        detail["code"],
    )
    return UTF8JSONResponse(
        status_code=503, content={"detail": detail}, headers=_run_headers(error)
    )


async def lifecycle_not_found_exception_handler(
    _request: Request, error: LifecycleNotFoundError
) -> UTF8JSONResponse:
    return UTF8JSONResponse(
        status_code=404,
        content={"detail": error.safe_detail()},
        headers=_run_headers(error),
    )


async def lifecycle_conflict_exception_handler(
    _request: Request, error: LifecycleConflictError
) -> UTF8JSONResponse:
    return UTF8JSONResponse(
        status_code=409,
        content={"detail": error.safe_detail()},
        headers=_run_headers(error),
    )


async def lifecycle_action_exception_handler(
    _request: Request, error: LifecycleActionError
) -> UTF8JSONResponse:
    status_code = 409 if error.code in {"lock_conflict"} else 422
    return UTF8JSONResponse(
        status_code=status_code,
        content={"detail": error.safe_detail()},
        headers=_run_headers(error),
    )


async def weather_unavailable_exception_handler(
    _request: Request, error: WeatherUnavailableError
) -> UTF8JSONResponse:
    detail = error.safe_detail()
    logger.error(
        "api.weather_unavailable session_id=%s provider=%s operation=%s "
        "category=%s code=%s retryable=%s",
        detail["session_id"],
        detail["provider"],
        detail["operation"],
        detail["category"],
        detail["code"],
        detail["retryable"],
    )
    return UTF8JSONResponse(
        status_code=503, content={"detail": detail}, headers=_run_headers(error)
    )


async def execution_budget_exception_handler(
    _request: Request, error: ExecutionBudgetExceeded
) -> UTF8JSONResponse:
    logger.warning(
        "api.execution_budget_exhausted run_id=%s limit=%s used=%s maximum=%s",
        error.run_id,
        error.limit,
        error.used,
        error.maximum,
    )
    return UTF8JSONResponse(
        status_code=503,
        content={"detail": error.safe_detail()},
        headers=_run_headers(error),
    )


async def run_not_found_exception_handler(
    _request: Request, error: RunNotFoundError
) -> UTF8JSONResponse:
    return UTF8JSONResponse(status_code=404, content={"detail": error.safe_detail()})


async def memory_not_found_exception_handler(
    _request: Request, error: MemoryNotFoundError
) -> UTF8JSONResponse:
    return UTF8JSONResponse(status_code=404, content={"detail": error.safe_detail()})


async def memory_conflict_exception_handler(
    _request: Request, error: MemoryConflictError
) -> UTF8JSONResponse:
    return UTF8JSONResponse(status_code=409, content={"detail": error.safe_detail()})


async def memory_policy_exception_handler(
    _request: Request, error: MemoryPolicyError
) -> UTF8JSONResponse:
    return UTF8JSONResponse(status_code=422, content={"detail": error.safe_detail()})


async def memory_forbidden_exception_handler(
    _request: Request, error: MemoryForbiddenError
) -> UTF8JSONResponse:
    return UTF8JSONResponse(status_code=403, content={"detail": error.safe_detail()})
async def application_exception_handler(_, error: ApplicationError) -> JSONResponse:
    status_code = {
        "not_found": 404,
        "forbidden": 403,
        "conflict": 409,
    }.get(error.code, 422)
    return UTF8JSONResponse(
        status_code=status_code,
        content={
            "code": error.code,
            "message": str(error),
            "retryable": error.retryable,
            "details": error.details,
        },
    )
