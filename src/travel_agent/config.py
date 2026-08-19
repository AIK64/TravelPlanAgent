from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping

from travel_agent.domain.tool_models import ProviderMode, UnknownFactPolicy


@dataclass(frozen=True, slots=True)
class Settings:
    provider: ProviderMode = ProviderMode.MOCK
    amap_api_key: str | None = field(default=None, repr=False)
    tool_timeout_seconds: float = 5.0
    tool_max_attempts: int = 3
    tool_backoff_base_seconds: float = 0.25
    tool_max_backoff_seconds: float = 2.0
    tool_max_concurrency: int = 5
    tool_cache_max_entries: int = 2048
    poi_cache_ttl_seconds: int = 3600
    route_cache_ttl_seconds: int = 300
    poi_query_limit: int = 10
    poi_candidate_limit: int = 12
    unknown_fact_policy: UnknownFactPolicy = UnknownFactPolicy.ASSUME_WITH_WARNING
    amap_driving_strategy: int = 32

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        source = os.environ if env is None else env
        settings = cls(
            provider=ProviderMode(source.get("TRAVEL_PROVIDER", "mock").strip().lower()),
            amap_api_key=source.get("AMAP_API_KEY", "").strip() or None,
            tool_timeout_seconds=float(source.get("TOOL_TIMEOUT_SECONDS", "5")),
            tool_max_attempts=int(source.get("TOOL_MAX_ATTEMPTS", "3")),
            tool_backoff_base_seconds=float(
                source.get("TOOL_BACKOFF_BASE_SECONDS", "0.25")
            ),
            tool_max_backoff_seconds=float(
                source.get("TOOL_MAX_BACKOFF_SECONDS", "2")
            ),
            tool_max_concurrency=int(source.get("TOOL_MAX_CONCURRENCY", "5")),
            tool_cache_max_entries=int(source.get("TOOL_CACHE_MAX_ENTRIES", "2048")),
            poi_cache_ttl_seconds=int(source.get("POI_CACHE_TTL_SECONDS", "3600")),
            route_cache_ttl_seconds=int(source.get("ROUTE_CACHE_TTL_SECONDS", "300")),
            poi_query_limit=int(source.get("POI_QUERY_LIMIT", "10")),
            poi_candidate_limit=int(source.get("POI_CANDIDATE_LIMIT", "12")),
            unknown_fact_policy=UnknownFactPolicy(
                source.get("UNKNOWN_FACT_POLICY", "assume_with_warning").strip().lower()
            ),
            amap_driving_strategy=int(source.get("AMAP_DRIVING_STRATEGY", "32")),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.provider is ProviderMode.AMAP and not self.amap_api_key:
            raise ValueError("AMAP_API_KEY is required when TRAVEL_PROVIDER=amap")
        if self.tool_timeout_seconds <= 0:
            raise ValueError("TOOL_TIMEOUT_SECONDS must be positive")
        if self.tool_max_attempts < 1:
            raise ValueError("TOOL_MAX_ATTEMPTS must be at least 1")
        if self.tool_backoff_base_seconds <= 0:
            raise ValueError("TOOL_BACKOFF_BASE_SECONDS must be positive")
        if self.tool_max_backoff_seconds <= 0:
            raise ValueError("TOOL_MAX_BACKOFF_SECONDS must be positive")
        if self.tool_max_concurrency <= 0:
            raise ValueError("TOOL_MAX_CONCURRENCY must be positive")
        if self.poi_cache_ttl_seconds <= 0:
            raise ValueError("POI_CACHE_TTL_SECONDS must be positive")
        if self.route_cache_ttl_seconds <= 0:
            raise ValueError("ROUTE_CACHE_TTL_SECONDS must be positive")
