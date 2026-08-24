from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from travel_agent.domain.tool_models import ToolResult, ToolStatus


@dataclass
class WeatherUnavailableError(Exception):
    result: ToolResult[Any]
    session_id: str

    def __post_init__(self) -> None:
        super().__init__("天气服务当前不可用")

    @classmethod
    def from_result(
        cls, result: ToolResult[Any], *, session_id: str
    ) -> "WeatherUnavailableError":
        if result.status is not ToolStatus.FAILED or result.error is None:
            raise ValueError("WeatherUnavailableError requires a failed ToolResult")
        return cls(result=result, session_id=session_id)

    def safe_detail(self) -> dict[str, str | bool]:
        error = self.result.error
        assert error is not None
        return {
            "session_id": self.session_id,
            "provider": self.result.provider,
            "operation": error.operation,
            "category": error.category.value,
            "code": error.code,
            "retryable": error.retryable,
            "message": error.safe_message,
        }
