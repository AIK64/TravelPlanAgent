from __future__ import annotations

from datetime import date, datetime, timezone

import httpx

from travel_agent.domain.tool_models import ToolErrorCategory
from travel_agent.domain.weather_models import DailyWeather, WeatherForecast, WeatherLocation
from travel_agent.tools.errors import ToolProviderError
from travel_agent.weather.policy import normalize_phenomenon, parse_temperature, parse_wind_level


class QWeatherProvider:
    name = "qweather"

    def __init__(self, client: httpx.AsyncClient, *, api_host: str, token: str) -> None:
        self._client = client
        self._api_host = api_host.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}

    async def resolve_location(self, destination: str) -> WeatherLocation:
        payload = await self._request(
            "weather.resolve_location",
            "/geo/v2/city/lookup",
            {"location": destination.strip(), "range": "cn", "number": 1},
        )
        locations = payload.get("location")
        if not isinstance(locations, list) or not locations or not isinstance(locations[0], dict):
            raise _invalid("weather.resolve_location", "weather_location_unresolved")
        selected = locations[0]
        try:
            return WeatherLocation(
                city_name=str(selected["name"]),
                adcode=str(selected["id"]),
                timezone=str(selected.get("tz") or "Asia/Shanghai"),
                provider=self.name,
                longitude=float(selected["lon"]),
                latitude=float(selected["lat"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise _invalid("weather.resolve_location", "invalid_location_response") from error

    async def get_forecast(
        self,
        location: WeatherLocation,
        *,
        start_date: date,
        end_date: date,
    ) -> WeatherForecast:
        if location.latitude is None or location.longitude is None:
            resolved = await self.resolve_location(location.city_name)
        else:
            resolved = location
        days_requested = min(10, max(1, (end_date - start_date).days + 1))
        payload = await self._request(
            "weather.get_forecast",
            f"/weather/v1/daily/{resolved.latitude:.4f}/{resolved.longitude:.4f}",
            {"days": days_requested, "localTime": "true", "lang": "zh"},
        )
        raw_days = payload.get("days")
        if not isinstance(raw_days, list):
            raise _invalid("weather.get_forecast", "invalid_weather_response")
        days: list[DailyWeather] = []
        for item in raw_days:
            if not isinstance(item, dict):
                continue
            try:
                forecast_date = date.fromisoformat(
                    str(item.get("forecastStartTime", ""))[:10]
                )
            except ValueError:
                continue
            if not start_date <= forecast_date <= end_date:
                continue
            daytime = item.get("daytime") if isinstance(item.get("daytime"), dict) else {}
            nighttime = item.get("nighttime") if isinstance(item.get("nighttime"), dict) else {}
            high = parse_temperature(_nested_value(item, "temperatureMax"))
            low = parse_temperature(_nested_value(item, "temperatureMin"))
            if high is not None and low is not None and high < low:
                high, low = low, high
            days.append(
                DailyWeather(
                    date=forecast_date,
                    day_phenomenon=normalize_phenomenon(_condition_text(daytime)),
                    night_phenomenon=normalize_phenomenon(_condition_text(nighttime)),
                    high_celsius=high,
                    low_celsius=low,
                    day_wind_level=parse_wind_level(_wind_scale(daytime)),
                    night_wind_level=parse_wind_level(_wind_scale(nighttime)),
                )
            )
        return WeatherForecast(
            location=resolved,
            provider=self.name,
            provider_reported_at=datetime.now(timezone.utc),
            days=tuple(days),
        )

    async def _request(
        self, operation: str, path: str, params: dict[str, object]
    ) -> dict[str, object]:
        try:
            response = await self._client.get(
                f"{self._api_host}{path}", params=params, headers=self._headers
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException as error:
            raise ToolProviderError.timeout(operation) from error
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            category = (
                ToolErrorCategory.AUTHENTICATION
                if status in {401, 403}
                else ToolErrorCategory.RATE_LIMIT
                if status == 429
                else ToolErrorCategory.UPSTREAM_UNAVAILABLE
            )
            raise ToolProviderError(
                category=category,
                code=f"http_{status}",
                operation=operation,
                retryable=category not in {ToolErrorCategory.AUTHENTICATION},
                safe_message="和风天气服务暂时不可用",
            ) from error
        except (httpx.RequestError, ValueError) as error:
            raise _invalid(operation, "connection_or_json_error") from error
        if not isinstance(payload, dict):
            raise _invalid(operation, "invalid_json_shape")
        code = str(payload.get("code", "200"))
        if code != "200":
            raise ToolProviderError(
                category=(
                    ToolErrorCategory.AUTHENTICATION
                    if code in {"401", "403"}
                    else ToolErrorCategory.RATE_LIMIT
                    if code == "429"
                    else ToolErrorCategory.UPSTREAM_UNAVAILABLE
                ),
                code=f"qweather_{code}",
                operation=operation,
                retryable=code not in {"401", "403"},
                safe_message="和风天气服务请求失败",
            )
        return payload


def _condition_text(section: dict[str, object]) -> object:
    condition = section.get("condition")
    return condition.get("text") if isinstance(condition, dict) else None


def _nested_value(item: dict[str, object], key: str) -> object:
    value = item.get(key)
    return value.get("value") if isinstance(value, dict) else value


def _wind_scale(section: dict[str, object]) -> object:
    wind = section.get("wind")
    return wind.get("scale") if isinstance(wind, dict) else None


def _invalid(operation: str, code: str) -> ToolProviderError:
    return ToolProviderError(
        category=ToolErrorCategory.INVALID_RESPONSE,
        code=code,
        operation=operation,
        retryable=True,
        safe_message="和风天气服务返回了无效响应",
    )
