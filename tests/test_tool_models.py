from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from travel_agent.domain.models import Coordinate
from travel_agent.domain.tool_models import (
    POIFacts,
    RouteQuery,
    ToolErrorCategory,
    ToolErrorInfo,
    ToolResult,
    ToolStatus,
    route_key,
)
from travel_agent.tools.errors import ToolProviderError, ToolUnavailableError


NOW = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)
A = Coordinate(longitude=120.15507, latitude=30.274085)
B = Coordinate(longitude=120.13874, latitude=30.23095)


def test_route_key_is_stable_and_directional():
    """若丢失方向，返程会错误复用去程的路线缓存。"""
    forward = RouteQuery(origin=A, destination=B)
    reverse = RouteQuery(origin=B, destination=A)

    assert route_key(forward) == route_key(forward.model_copy())
    assert route_key(forward) != route_key(reverse)


def test_tool_result_never_requires_raw_payload():
    """Graph State 只应接收归一化事实，不应携带供应商原始响应。"""
    result = ToolResult[list[POIFacts]](
        status=ToolStatus.SUCCESS,
        data=[],
        provider="mock",
        fetched_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        cache_hit=False,
        attempt_count=1,
    )

    assert "raw" not in result.model_dump()


def test_tool_result_rejects_success_without_data():
    """若成功结果没有事实数据，后续节点会把工具失败误判为可用结果。"""
    with pytest.raises(ValidationError, match="data"):
        ToolResult[object](status=ToolStatus.SUCCESS, provider="mock")


def test_tool_error_info_and_unavailable_detail_expose_only_safe_fields():
    """供应商异常转换后，API 层只能看到结构化、安全的故障信息。"""
    provider_error = ToolProviderError.timeout("poi.search")
    info = ToolErrorInfo.from_provider_error(provider_error)
    result = ToolResult[object].failed(provider="amap", error=info, attempt_count=3)
    unavailable = ToolUnavailableError.from_result(result, thread_id="thread-42")

    assert info.category is ToolErrorCategory.TIMEOUT
    assert unavailable.safe_detail() == {
        "thread_id": "thread-42",
        "provider": "amap",
        "operation": "poi.search",
        "category": "timeout",
        "code": "timeout",
        "retryable": True,
        "message": "The provider timed out. Please try again.",
    }
