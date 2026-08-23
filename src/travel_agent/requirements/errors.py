from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RequirementErrorCategory(StrEnum):
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
class RequirementProviderError(Exception):
    category: RequirementErrorCategory
    code: str
    retryable: bool
    safe_message: str
    retry_after_seconds: float | None = None

    def __post_init__(self) -> None:
        Exception.__init__(self, self.safe_message)


@dataclass
class RequirementUnavailableError(Exception):
    provider: str
    model: str
    category: RequirementErrorCategory
    code: str
    retryable: bool
    safe_message: str
    thread_id: str
    attempt_count: int

    def __post_init__(self) -> None:
        Exception.__init__(self, "The requirement model is unavailable.")

    def safe_detail(self) -> dict[str, str | bool]:
        return {
            "code": self.code,
            "provider": self.provider,
            "model": self.model,
            "category": self.category.value,
            "retryable": self.retryable,
            "thread_id": self.thread_id,
            "message": self.safe_message,
        }


@dataclass
class ClarificationThreadNotFoundError(Exception):
    thread_id: str

    def __post_init__(self) -> None:
        Exception.__init__(self, "The clarification thread was not found.")

    def safe_detail(self) -> dict[str, str]:
        return {
            "code": "clarification_thread_not_found",
            "thread_id": self.thread_id,
            "message": "没有找到可恢复的规划线程",
        }


@dataclass
class ClarificationResumeConflictError(Exception):
    thread_id: str
    code: str = "clarification_resume_conflict"

    def __post_init__(self) -> None:
        Exception.__init__(self, "The clarification thread cannot be resumed.")

    def safe_detail(self) -> dict[str, str]:
        return {
            "code": self.code,
            "thread_id": self.thread_id,
            "message": "该澄清请求已过期、已处理或线程当前不可恢复",
        }
