from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from travel_agent.domain.tool_models import ToolErrorCategory
from travel_agent.domain.weather_models import (
    DailyWeather,
    WeatherForecast,
    WeatherLocation,
)
from travel_agent.tools.errors import ToolProviderError
from travel_agent.tools.providers.amap import AMapClient
from travel_agent.weather.policy import (
    normalize_phenomenon,
    parse_temperature,
    parse_wind_level,
)


CHINA_TZ = timezone(timedelta(hours=8))


def _normalize_place(value: str) -> str:
    return value.strip().casefold().removesuffix("市").removesuffix("区").removesuffix("县")


def _provider_error(
    *, code: str, operation: str, message: str, invalid_request: bool = False
) -> ToolProviderError:
    return ToolProviderError(
        category=(
            ToolErrorCategory.INVALID_REQUEST
            if invalid_request
            else ToolErrorCategory.INVALID_RESPONSE
        ),
        code=code,
        operation=operation,
        retryable=False,
        safe_message=message,
    )


class AMapWeatherProvider:
    name = "amap"

    def __init__(self, client: AMapClient) -> None:
        self._client = client

    async def resolve_location(self, destination: str) -> WeatherLocation:
        operation = "weather.resolve_location"
        payload = await self._client.request_json(
            operation,
            "/v3/config/district",
            {
                "keywords": destination.strip(),
                "subdistrict": 0,
                "extensions": "base",
                "output": "JSON",
            },
        )
        districts = payload.get("districts")
        if not isinstance(districts, list):
            raise _provider_error(
                code="invalid_district_response",
                operation=operation,
                message="天气地点服务返回了无效响应",
            )
        candidates = [item for item in districts if isinstance(item, dict)]
        needle = _normalize_place(destination)
        exact = [
            item
            for item in candidates
            if _normalize_place(str(item.get("name", ""))) == needle
        ]
        selected = exact if exact else candidates
        unique = {
            str(item.get("adcode", "")): item
            for item in selected
            if str(item.get("adcode", "")).isdigit()
            and len(str(item.get("adcode", ""))) == 6
        }
        if len(unique) != 1:
            raise _provider_error(
                code="weather_location_unresolved",
                operation=operation,
                message="无法唯一解析天气查询地点",
                invalid_request=True,
            )
        adcode, item = next(iter(unique.items()))
        name = str(item.get("name", "")).strip()
        if not name:
            raise _provider_error(
                code="invalid_district_response",
                operation=operation,
                message="天气地点服务缺少标准化名称",
            )
        return WeatherLocation(
            city_name=name,
            adcode=adcode,
            provider=self.name,
        )

    async def get_forecast(
        self,
        location: WeatherLocation,
        *,
        start_date: date,
        end_date: date,
    ) -> WeatherForecast:
        operation = "weather.get_forecast"
        payload = await self._client.request_json(
            operation,
            "/v3/weather/weatherInfo",
            {
                "city": location.adcode,
                "extensions": "all",
                "output": "JSON",
            },
        )
        raw_forecasts = payload.get("forecasts", payload.get("forecast"))
        if not isinstance(raw_forecasts, list) or not raw_forecasts:
            raise _provider_error(
                code="invalid_weather_response",
                operation=operation,
                message="天气服务返回了无效预报结构",
            )
        forecast = raw_forecasts[0]
        if not isinstance(forecast, dict):
            raise _provider_error(
                code="invalid_weather_response",
                operation=operation,
                message="天气服务返回了无效预报对象",
            )
        raw_casts = forecast.get("casts")
        if not isinstance(raw_casts, list):
            raise _provider_error(
                code="invalid_weather_response",
                operation=operation,
                message="天气服务缺少日级预报",
            )
        days: list[DailyWeather] = []
        for raw in raw_casts:
            if not isinstance(raw, dict):
                continue
            try:
                forecast_date = date.fromisoformat(str(raw.get("date", "")))
            except ValueError:
                continue
            if not start_date <= forecast_date <= end_date:
                continue
            high = parse_temperature(raw.get("daytemp"))
            low = parse_temperature(raw.get("nighttemp"))
            if high is not None and low is not None and high < low:
                high, low = low, high
            days.append(
                DailyWeather(
                    date=forecast_date,
                    day_phenomenon=normalize_phenomenon(raw.get("dayweather")),
                    night_phenomenon=normalize_phenomenon(raw.get("nightweather")),
                    high_celsius=high,
                    low_celsius=low,
                    day_wind_level=parse_wind_level(raw.get("daypower")),
                    night_wind_level=parse_wind_level(raw.get("nightpower")),
                )
            )
        reported_at = _parse_reporttime(forecast.get("reporttime"))
        return WeatherForecast(
            location=location,
            provider=self.name,
            provider_reported_at=reported_at,
            days=tuple(sorted(days, key=lambda item: item.date)),
        )


def _parse_reporttime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return parsed.replace(tzinfo=CHINA_TZ)
