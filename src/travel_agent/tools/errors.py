from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from travel_agent.domain.tool_models import (
    ToolErrorCategory,
    ToolResult,
    ToolStatus,
)


@dataclass
class ToolProviderError(Exception):
    category: ToolErrorCategory
    code: str
    operation: str
    retryable: bool
    safe_message: str
    retry_after_seconds: float | None = None

    def __post_init__(self) -> None:
        super().__init__(self.safe_message)

    @classmethod
    def timeout(cls, operation: str) -> "ToolProviderError":
        return cls(
            category=ToolErrorCategory.TIMEOUT,
            code="timeout",
            operation=operation,
            retryable=True,
            safe_message="The provider timed out. Please try again.",
        )

    @classmethod
    def authentication(cls, operation: str) -> "ToolProviderError":
        return cls(
            category=ToolErrorCategory.AUTHENTICATION,
            code="authentication",
            operation=operation,
            retryable=False,
            safe_message="The provider authentication failed. Please check configuration.",
        )


@dataclass(frozen=True, slots=True)
class ToolRetryExhausted(Exception):
    """工具调用在重试预算耗尽或遇到永久错误后的结构化失败。"""

    last_error: ToolProviderError
    attempts: int

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("attempts must be at least 1")
        Exception.__init__(
            self,
            f"Tool provider retry exhausted after {self.attempts} attempts: "
            f"{self.last_error.safe_message}"
        )


@dataclass
class ToolUnavailableError(Exception):
    result: ToolResult[Any]
    thread_id: str

    def __post_init__(self) -> None:
        super().__init__("The requested tool is unavailable.")

    @classmethod
    def from_result(
        cls, result: ToolResult[Any], thread_id: str
    ) -> "ToolUnavailableError":
        if result.status is not ToolStatus.FAILED or result.error is None:
            raise ValueError("ToolUnavailableError requires a failed ToolResult")
        return cls(result=result, thread_id=thread_id)

    def safe_detail(self) -> dict[str, str | bool]:
        error = self.result.error
        assert error is not None
        return {
            "thread_id": self.thread_id,
            "provider": self.result.provider,
            "operation": error.operation,
            "category": error.category.value,
            "code": error.code,
            "retryable": error.retryable,
            "message": error.safe_message,
        }
