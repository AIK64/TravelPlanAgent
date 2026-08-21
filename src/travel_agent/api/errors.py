from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import JSONResponse

from travel_agent.tools.errors import ToolUnavailableError


logger = logging.getLogger(__name__)


class UTF8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"


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
    )
