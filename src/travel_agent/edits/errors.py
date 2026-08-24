from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class EditErrorCategory(StrEnum):
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    RATE_LIMIT = "rate_limit"
    AUTHENTICATION = "authentication"
    PERMISSION = "permission"
    INVALID_REQUEST = "invalid_request"
    INVALID_RESPONSE = "invalid_response"
    REFUSAL = "refusal"
    INCOMPLETE = "incomplete"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"


@dataclass
class EditProviderError(Exception):
    category: EditErrorCategory
    code: str
    retryable: bool
    safe_message: str
    retry_after_seconds: float | None = None

    def __post_init__(self) -> None:
        Exception.__init__(self, self.safe_message)


@dataclass
class EditUnavailableError(Exception):
    provider: str
    model: str
    category: EditErrorCategory
    code: str
    retryable: bool
    safe_message: str
    session_id: str
    attempt_count: int

    def __post_init__(self) -> None:
        Exception.__init__(self, "The edit model is unavailable.")

    def safe_detail(self) -> dict[str, str | bool]:
        return {
            "code": self.code,
            "provider": self.provider,
            "model": self.model,
            "category": self.category.value,
            "retryable": self.retryable,
            "session_id": self.session_id,
            "message": self.safe_message,
        }


def map_openai_compatible_error(error: Exception) -> EditProviderError:
    name = type(error).__name__.casefold()
    status = getattr(error, "status_code", None)
    if "timeout" in name:
        category, code, retryable = EditErrorCategory.TIMEOUT, "timeout", True
    elif status == 429 or "ratelimit" in name or "rate_limit" in name:
        category, code, retryable = EditErrorCategory.RATE_LIMIT, "rate_limit", True
    elif status == 401:
        category, code, retryable = EditErrorCategory.AUTHENTICATION, "authentication", False
    elif status == 403:
        category, code, retryable = EditErrorCategory.PERMISSION, "permission", False
    elif "connection" in name:
        category, code, retryable = EditErrorCategory.CONNECTION, "connection", True
    elif isinstance(status, int) and status >= 500:
        category, code, retryable = EditErrorCategory.UPSTREAM_UNAVAILABLE, "upstream_unavailable", True
    else:
        category, code, retryable = EditErrorCategory.INVALID_RESPONSE, "upstream_error", True
    return EditProviderError(category, code, retryable, "计划编辑解析服务暂时不可用")

