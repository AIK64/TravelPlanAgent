from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

from travel_agent.domain.weather_models import WeatherForecast, WeatherLocation


@runtime_checkable
class WeatherProvider(Protocol):
    name: str

    async def resolve_location(self, destination: str) -> WeatherLocation:
        raise NotImplementedError

    async def get_forecast(
        self,
        location: WeatherLocation,
        *,
        start_date: date,
        end_date: date,
    ) -> WeatherForecast:
        raise NotImplementedError
