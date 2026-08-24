from __future__ import annotations

from travel_agent.critique.errors import CriticErrorCategory, CriticProviderError


def map_provider_error(error: Exception) -> CriticProviderError:
    name = type(error).__name__.casefold()
    status_code = getattr(error, "status_code", None)
    if "timeout" in name:
        category, code, retryable = CriticErrorCategory.TIMEOUT, "timeout", True
    elif status_code == 429 or "ratelimit" in name or "rate_limit" in name:
        category, code, retryable = CriticErrorCategory.RATE_LIMIT, "rate_limit", True
    elif status_code == 401 or "authentication" in name:
        category, code, retryable = CriticErrorCategory.AUTHENTICATION, "authentication", False
    elif status_code == 403 or "permission" in name:
        category, code, retryable = CriticErrorCategory.PERMISSION, "permission", False
    elif "connection" in name:
        category, code, retryable = CriticErrorCategory.CONNECTION, "connection", True
    elif isinstance(status_code, int) and status_code >= 500:
        category, code, retryable = (
            CriticErrorCategory.UPSTREAM_UNAVAILABLE,
            "upstream_unavailable",
            True,
        )
    elif isinstance(status_code, int) and status_code >= 400:
        category, code, retryable = CriticErrorCategory.INVALID_REQUEST, "invalid_request", False
    else:
        category, code, retryable = CriticErrorCategory.UPSTREAM_UNAVAILABLE, "upstream_error", True
    return CriticProviderError(
        category=category,
        code=code,
        retryable=retryable,
        safe_message="软质量评审服务暂时不可用",
    )


def usage_value(usage: object, name: str) -> int | None:
    if usage is None:
        return None
    value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
    return value if isinstance(value, int) and value >= 0 else None

