from __future__ import annotations

import pytest

from travel_agent.config import CheckpointBackend, CriticProviderMode, Settings
from travel_agent.domain.tool_models import ProviderMode
from travel_agent.requirements.models import RequirementProviderMode


def test_settings_default_to_mock():
    """若默认供应商误改为真实服务，离线演示会产生外部调用。"""
    settings = Settings.from_env({})

    assert settings.provider is ProviderMode.MOCK
    assert settings.requirement_provider is RequirementProviderMode.MOCK
    assert settings.critic_provider is CriticProviderMode.MOCK
    assert settings.requirement_model == "mock-requirement-v1"
    assert settings.requirement_max_attempts == 2
    assert settings.tool_max_attempts == 3
    assert settings.poi_query_limit == 10
    assert settings.poi_candidate_limit == 12
    assert settings.poi_max_queries == 12
    assert settings.amap_driving_strategy == 32
    assert settings.max_walking_leg_meters == 1_500
    assert settings.use_real_walking_routes is True
    assert settings.optimization_variant_count == 3


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


def test_openai_requirement_provider_requires_key_and_model():
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        Settings.from_env(
            {
                "REQUIREMENT_PROVIDER": "openai",
                "REQUIREMENT_MODEL": "test-model",
            }
        )

    with pytest.raises(ValueError, match="REQUIREMENT_MODEL"):
        Settings.from_env(
            {
                "REQUIREMENT_PROVIDER": "openai",
                "OPENAI_API_KEY": "test-key",
                "REQUIREMENT_MODEL": "",
            }
        )

    with pytest.raises(ValueError, match="REQUIREMENT_MODEL"):
        Settings.from_env(
            {
                "REQUIREMENT_PROVIDER": "openai",
                "OPENAI_API_KEY": "test-key",
            }
        )


def test_openai_requirement_configuration_is_explicit_and_secret_safe():
    settings = Settings.from_env(
        {
            "REQUIREMENT_PROVIDER": "openai",
            "OPENAI_API_KEY": "openai-test-key",
            "REQUIREMENT_MODEL": "test-model",
            "REQUIREMENT_TIMEOUT_SECONDS": "12",
            "REQUIREMENT_MAX_ATTEMPTS": "3",
        }
    )

    assert settings.requirement_provider is RequirementProviderMode.OPENAI
    assert settings.requirement_model == "test-model"
    assert settings.requirement_timeout_seconds == 12
    assert settings.requirement_max_attempts == 3
    assert "openai-test-key" not in repr(settings)


def test_deepseek_requirement_provider_requires_key_and_model():
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        Settings.from_env(
            {
                "REQUIREMENT_PROVIDER": "deepseek",
                "DEEPSEEK_MODEL": "deepseek-v4-flash",
            }
        )

    with pytest.raises(ValueError, match="DEEPSEEK_MODEL"):
        Settings.from_env(
            {
                "REQUIREMENT_PROVIDER": "deepseek",
                "DEEPSEEK_API_KEY": "test-key",
            }
        )


def test_deepseek_configuration_is_explicit_and_secret_safe():
    settings = Settings.from_env(
        {
            "REQUIREMENT_PROVIDER": "deepseek",
            "DEEPSEEK_API_KEY": "deepseek-test-key",
            "DEEPSEEK_MODEL": "deepseek-v4-flash",
        }
    )

    assert settings.requirement_provider is RequirementProviderMode.DEEPSEEK
    assert settings.deepseek_model == "deepseek-v4-flash"
    assert settings.deepseek_base_url == "https://api.deepseek.com"
    assert "deepseek-test-key" not in repr(settings)


@pytest.mark.parametrize("model", ["deepseek-chat", "deepseek-reasoner"])
def test_deepseek_rejects_retired_model_aliases(model: str):
    with pytest.raises(ValueError, match="retired alias"):
        Settings.from_env(
            {
                "REQUIREMENT_PROVIDER": "deepseek",
                "DEEPSEEK_API_KEY": "test-key",
                "DEEPSEEK_MODEL": model,
            }
        )


@pytest.mark.parametrize(
    "base_url",
    [
        "http://api.deepseek.com",
        "https://user:secret@api.deepseek.com",
        "https://api.deepseek.com?key=secret",
    ],
)
def test_deepseek_base_url_must_be_safe_https(base_url: str):
    with pytest.raises(ValueError, match="DEEPSEEK_BASE_URL"):
        Settings.from_env(
            {
                "REQUIREMENT_PROVIDER": "deepseek",
                "DEEPSEEK_API_KEY": "test-key",
                "DEEPSEEK_MODEL": "deepseek-v4-flash",
                "DEEPSEEK_BASE_URL": base_url,
            }
        )


def test_settings_parse_non_default_planning_policy():
    """防止公开规划设置只存在于环境变量示例而未进入配置对象。"""
    settings = Settings.from_env(
        {
            "POI_QUERY_LIMIT": "1",
            "POI_CANDIDATE_LIMIT": "1",
            "POI_MAX_QUERIES": "2",
            "AMAP_DRIVING_STRATEGY": "7",
            "USE_REAL_WALKING_ROUTES": "false",
            "OPTIMIZATION_VARIANT_COUNT": "1",
        }
    )

    assert (
        settings.poi_query_limit,
        settings.poi_candidate_limit,
        settings.poi_max_queries,
        settings.amap_driving_strategy,
    ) == (1, 1, 2, 7)
    assert settings.use_real_walking_routes is False
    assert settings.optimization_variant_count == 1


def test_settings_rejects_invalid_boolean():
    with pytest.raises(ValueError, match="USE_REAL_WALKING_ROUTES"):
        Settings.from_env({"USE_REAL_WALKING_ROUTES": "sometimes"})


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
        ("POI_QUERY_LIMIT", "0"),
        ("POI_QUERY_LIMIT", "26"),
        ("POI_CANDIDATE_LIMIT", "0"),
        ("POI_CANDIDATE_LIMIT", "101"),
        ("POI_MAX_QUERIES", "0"),
        ("POI_MAX_QUERIES", "101"),
        ("AMAP_DRIVING_STRATEGY", "-1"),
        ("REQUIREMENT_TIMEOUT_SECONDS", "0"),
        ("REQUIREMENT_MAX_ATTEMPTS", "0"),
        ("REQUIREMENT_BACKOFF_BASE_SECONDS", "-1"),
        ("REQUIREMENT_MAX_BACKOFF_SECONDS", "-1"),
        ("CRITIC_TIMEOUT_SECONDS", "0"),
        ("CRITIC_MAX_ATTEMPTS", "0"),
        ("CRITIC_GROUNDING_MAX_ATTEMPTS", "3"),
        ("CRITIC_MAX_INPUT_CHARS", "0"),
        ("CRITIC_MAX_OUTPUT_TOKENS", "0"),
        ("CRITIC_QUALITY_THRESHOLD", "101"),
        ("CRITIC_MIN_IMPROVEMENT", "-1"),
        ("MAX_SOFT_REPLAN_ROUNDS", "2"),
    ],
)
def test_settings_reject_invalid_execution_budget(name: str, value: str):
    """非正执行预算会让重试、缓存或并发语义失效，必须被拒绝。"""
    with pytest.raises(ValueError):
        Settings.from_env({name: value})


def test_sqlite_checkpoint_configuration_is_explicit():
    settings = Settings.from_env(
        {
            "CHECKPOINT_BACKEND": "sqlite",
            "CHECKPOINT_SQLITE_PATH": ".data/test-checkpoints.sqlite3",
        }
    )

    assert settings.checkpoint_backend is CheckpointBackend.SQLITE
    assert settings.checkpoint_sqlite_path == ".data/test-checkpoints.sqlite3"


def test_sqlite_checkpoint_requires_path():
    with pytest.raises(ValueError, match="CHECKPOINT_SQLITE_PATH"):
        Settings.from_env(
            {
                "CHECKPOINT_BACKEND": "sqlite",
                "CHECKPOINT_SQLITE_PATH": " ",
            }
        )


def test_deepseek_critic_requires_independent_explicit_model():
    with pytest.raises(ValueError, match="CRITIC_MODEL"):
        Settings.from_env(
            {
                "CRITIC_PROVIDER": "deepseek",
                "DEEPSEEK_API_KEY": "secret",
            }
        )

    settings = Settings.from_env(
        {
            "CRITIC_PROVIDER": "deepseek",
            "CRITIC_MODEL": "deepseek-explicit-model",
            "DEEPSEEK_API_KEY": "secret",
        }
    )
    assert settings.critic_provider is CriticProviderMode.DEEPSEEK
    assert settings.critic_model == "deepseek-explicit-model"


def test_disabled_critic_requires_no_model_key():
    settings = Settings.from_env({"CRITIC_PROVIDER": "disabled"})
    assert settings.critic_provider is CriticProviderMode.DISABLED
