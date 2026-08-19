from __future__ import annotations

import pytest

from travel_agent.config import Settings
from travel_agent.domain.tool_models import ProviderMode


def test_settings_default_to_mock():
    """若默认供应商误改为真实服务，离线演示会产生外部调用。"""
    settings = Settings.from_env({})

    assert settings.provider is ProviderMode.MOCK
    assert settings.tool_max_attempts == 3
    assert settings.poi_candidate_limit == 12


def test_amap_requires_key():
    """真实 AMap 模式必须在启动配置阶段拒绝无凭证状态。"""
    with pytest.raises(ValueError, match="AMAP_API_KEY"):
        Settings.from_env({"TRAVEL_PROVIDER": "amap"})


def test_amap_accepts_non_empty_key():
    """提供有效凭证时应保留明确选择的真实 Provider。"""
    settings = Settings.from_env(
        {"TRAVEL_PROVIDER": "amap", "AMAP_API_KEY": "test-key"}
    )

    assert settings.provider is ProviderMode.AMAP
    assert settings.amap_api_key == "test-key"


def test_settings_repr_does_not_expose_amap_api_key():
    """若配置被日志记录，真实 Provider 凭证不得出现在日志中。"""
    settings = Settings.from_env(
        {"TRAVEL_PROVIDER": "amap", "AMAP_API_KEY": "test-key"}
    )

    assert "test-key" not in repr(settings)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("TOOL_TIMEOUT_SECONDS", "0"),
        ("TOOL_BACKOFF_BASE_SECONDS", "0"),
        ("TOOL_MAX_BACKOFF_SECONDS", "0"),
        ("TOOL_MAX_ATTEMPTS", "0"),
        ("TOOL_MAX_CONCURRENCY", "0"),
        ("POI_CACHE_TTL_SECONDS", "0"),
        ("ROUTE_CACHE_TTL_SECONDS", "0"),
    ],
)
def test_settings_reject_invalid_execution_budget(name: str, value: str):
    """非正执行预算会让重试、缓存或并发语义失效，必须被拒绝。"""
    with pytest.raises(ValueError):
        Settings.from_env({name: value})
