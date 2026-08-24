from __future__ import annotations

from datetime import date, timedelta

from travel_agent.domain.weather_models import (
    DailyWeather,
    WeatherForecast,
    WeatherLocation,
    WeatherPhenomenon,
)
from travel_agent.tools.errors import ToolProviderError
from travel_agent.domain.tool_models import ToolErrorCategory


class MockWeatherProvider:
    """固定日期相对场景：首个行程日降雨，其余日期晴。"""

    name = "mock"

    async def resolve_location(self, destination: str) -> WeatherLocation:
        normalized = destination.strip().removesuffix("市")
        if normalized != "杭州":
            raise ToolProviderError(
                category=ToolErrorCategory.INVALID_REQUEST,
                code="weather_location_unresolved",
                operation="weather.resolve_location",
                retryable=False,
                safe_message="无法唯一解析天气查询地点",
            )
        return WeatherLocation(
            city_name="杭州市",
            adcode="330100",
            provider=self.name,
        )

    async def get_forecast(
        self,
        location: WeatherLocation,
        *,
        start_date: date,
        end_date: date,
    ) -> WeatherForecast:
        days: list[DailyWeather] = []
        current = start_date
        index = 0
        while current <= end_date:
            rainy = index == 0
            days.append(
                DailyWeather(
                    date=current,
                    day_phenomenon=(
                        WeatherPhenomenon.RAIN
                        if rainy
                        else WeatherPhenomenon.CLEAR
                    ),
                    night_phenomenon=WeatherPhenomenon.CLOUDY,
                    high_celsius=27 if rainy else 30,
                    low_celsius=20,
                    day_wind_level=3,
                    night_wind_level=3,
                )
            )
            current += timedelta(days=1)
            index += 1
        return WeatherForecast(
            location=location,
            provider=self.name,
            days=tuple(days),
        )
