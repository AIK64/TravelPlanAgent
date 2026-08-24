from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Awaitable, Callable, TypeVar

from travel_agent.domain.tool_models import (
    POIFacts,
    POISearchQuery,
    RouteQuery,
    RouteResult,
    ToolErrorCategory,
)
from travel_agent.execution.context import current_run_context
from travel_agent.execution.models import TraceEventType
from travel_agent.tools.errors import ToolProviderError
from travel_agent.tools.protocols import POIProvider, RouteProvider
from travel_agent.weather.protocols import WeatherProvider
from travel_agent.domain.weather_models import WeatherForecast, WeatherLocation


T = TypeVar("T")
logger = logging.getLogger(__name__)
_FAILOVER_CATEGORIES = {
    ToolErrorCategory.TIMEOUT,
    ToolErrorCategory.CONNECTION,
    ToolErrorCategory.RATE_LIMIT,
    ToolErrorCategory.INVALID_RESPONSE,
    ToolErrorCategory.UPSTREAM_UNAVAILABLE,
}


@dataclass(slots=True)
class _Circuit:
    failures: int = 0
    open_until: datetime | None = None


class _ProviderChain:
    def __init__(
        self,
        providers: tuple[object, ...],
        *,
        failure_threshold: int = 3,
        recovery_seconds: int = 30,
    ) -> None:
        if not providers:
            raise ValueError("provider chain requires at least one provider")
        if failure_threshold < 1 or recovery_seconds < 1:
            raise ValueError("invalid circuit breaker policy")
        self.providers = providers
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self._circuits = {self._name(item): _Circuit() for item in providers}

    @property
    def name(self) -> str:
        return "->".join(self._name(item) for item in self.providers)

    async def _execute(
        self,
        operation: str,
        call: Callable[[object], Awaitable[T]],
    ) -> T:
        last_error: ToolProviderError | None = None
        now = datetime.now(timezone.utc)
        available = [
            provider
            for provider in self.providers
            if not self._is_open(self._name(provider), now)
        ]
        if not available:
            available = [self.providers[-1]]
        for index, provider in enumerate(available):
            name = self._name(provider)
            parent_event_id = self._trace(
                TraceEventType.PROVIDER_ATTEMPT_STARTED,
                operation,
                "started",
                {"provider": name, "chain_position": index + 1},
            )
            started = perf_counter()
            try:
                result = await call(provider)
            except ToolProviderError as error:
                last_error = error
                self._note_failure(name)
                self._trace(
                    TraceEventType.PROVIDER_ATTEMPT_COMPLETED,
                    operation,
                    "failed",
                    {
                        "provider": name,
                        "category": error.category.value,
                        "code": error.code,
                    },
                    parent_event_id=parent_event_id,
                    duration_ms=round((perf_counter() - started) * 1000),
                )
                can_failover = (
                    error.category in _FAILOVER_CATEGORIES
                    and index + 1 < len(available)
                )
                if not can_failover:
                    break
                next_name = self._name(available[index + 1])
                self._trace(
                    TraceEventType.PROVIDER_FALLBACK_SELECTED,
                    operation,
                    "selected",
                    {
                        "from_provider": name,
                        "to_provider": next_name,
                        "reason": error.category.value,
                    },
                )
                logger.warning(
                    "provider.fallback_selected | operation=%s from=%s to=%s reason=%s",
                    operation,
                    name,
                    next_name,
                    error.category.value,
                )
                continue
            self._note_success(name)
            self._trace(
                TraceEventType.PROVIDER_ATTEMPT_COMPLETED,
                operation,
                "success",
                {"provider": name},
                parent_event_id=parent_event_id,
                duration_ms=round((perf_counter() - started) * 1000),
            )
            return result
        assert last_error is not None
        self._trace(
            TraceEventType.PROVIDER_CHAIN_EXHAUSTED,
            operation,
            "failed",
            {"category": last_error.category.value, "code": last_error.code},
        )
        raise last_error

    def _is_open(self, name: str, now: datetime) -> bool:
        circuit = self._circuits[name]
        if circuit.open_until is None:
            return False
        if now >= circuit.open_until:
            circuit.open_until = None
            circuit.failures = 0
            return False
        return True

    def _note_failure(self, name: str) -> None:
        circuit = self._circuits[name]
        circuit.failures += 1
        if circuit.failures >= self.failure_threshold:
            circuit.open_until = datetime.now(timezone.utc) + timedelta(
                seconds=self.recovery_seconds
            )

    def _note_success(self, name: str) -> None:
        self._circuits[name] = _Circuit()

    @staticmethod
    def _name(provider: object) -> str:
        return str(getattr(provider, "name", provider.__class__.__name__))

    @staticmethod
    def _trace(
        event_type: TraceEventType,
        operation: str,
        status: str,
        attributes: dict[str, str | int],
        *,
        parent_event_id: str | None = None,
        duration_ms: int | None = None,
    ) -> str | None:
        run = current_run_context()
        if run is None:
            return None
        event = run.trace.record(
            event_type,
            operation=operation,
            status=status,
            attributes=attributes,
            parent_event_id=parent_event_id,
            duration_ms=duration_ms,
        )
        return event.event_id if event else None


class POIProviderChain(_ProviderChain):
    def __init__(self, providers: tuple[POIProvider, ...], **kwargs) -> None:
        super().__init__(providers, **kwargs)

    async def search_pois(self, query: POISearchQuery) -> list[POIFacts]:
        return await self._execute(
            "poi.search", lambda provider: provider.search_pois(query)
        )


class RouteProviderChain(_ProviderChain):
    def __init__(self, providers: tuple[RouteProvider, ...], **kwargs) -> None:
        super().__init__(providers, **kwargs)

    async def get_driving_route(self, query: RouteQuery) -> RouteResult:
        return await self._execute(
            "route.get_driving", lambda provider: provider.get_driving_route(query)
        )

    async def get_walking_route(self, query: RouteQuery) -> RouteResult:
        return await self._execute(
            "route.get_walking", lambda provider: provider.get_walking_route(query)
        )


class WeatherProviderChain(_ProviderChain):
    def __init__(self, providers: tuple[WeatherProvider, ...], **kwargs) -> None:
        super().__init__(providers, **kwargs)

    async def resolve_location(self, destination: str) -> WeatherLocation:
        return await self._execute(
            "weather.resolve_location",
            lambda provider: provider.resolve_location(destination),
        )

    async def get_forecast(
        self,
        location: WeatherLocation,
        *,
        start_date,
        end_date,
    ) -> WeatherForecast:
        return await self._execute(
            "weather.get_forecast",
            lambda provider: provider.get_forecast(
                location, start_date=start_date, end_date=end_date
            ),
        )
