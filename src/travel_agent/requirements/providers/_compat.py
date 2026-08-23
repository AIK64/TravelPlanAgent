from __future__ import annotations

from travel_agent.requirements.errors import (
    RequirementErrorCategory,
    RequirementProviderError,
)


def map_openai_compatible_error(error: Exception) -> RequirementProviderError:
    """将 OpenAI SDK 及兼容服务错误归一化，且不暴露上游正文。"""
    name = type(error).__name__.casefold()
    status_code = getattr(error, "status_code", None)
    if "timeout" in name:
        category = RequirementErrorCategory.TIMEOUT
        code = "timeout"
        retryable = True
    elif status_code == 429 or "ratelimit" in name or "rate_limit" in name:
        category = RequirementErrorCategory.RATE_LIMIT
        code = "rate_limit"
        retryable = True
    elif status_code == 401 or "authentication" in name:
        category = RequirementErrorCategory.AUTHENTICATION
        code = "authentication"
        retryable = False
    elif status_code == 403 or "permission" in name:
        category = RequirementErrorCategory.PERMISSION
        code = "permission"
        retryable = False
    elif "connection" in name:
        category = RequirementErrorCategory.CONNECTION
        code = "connection"
        retryable = True
    elif isinstance(status_code, int) and status_code >= 500:
        category = RequirementErrorCategory.UPSTREAM_UNAVAILABLE
        code = "upstream_unavailable"
        retryable = True
    elif isinstance(status_code, int) and status_code >= 400:
        category = RequirementErrorCategory.INVALID_REQUEST
        code = "invalid_request"
        retryable = False
    else:
        category = RequirementErrorCategory.UPSTREAM_UNAVAILABLE
        code = "upstream_error"
        retryable = True
    return RequirementProviderError(
        category=category,
        code=code,
        retryable=retryable,
        safe_message="需求解析服务暂时不可用，请稍后重试",
    )


def usage_value(usage: object, name: str) -> int | None:
    if usage is None:
        return None
    if isinstance(usage, dict):
        value = usage.get(name)
    else:
        value = getattr(usage, name, None)
    return value if isinstance(value, int) and value >= 0 else None
