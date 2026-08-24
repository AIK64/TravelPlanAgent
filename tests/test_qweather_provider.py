from __future__ import annotations

from datetime import date

import httpx
import pytest

from travel_agent.domain.tool_models import ToolErrorCategory
from travel_agent.domain.weather_models import WeatherPhenomenon
from travel_agent.tools.errors import ToolProviderError
from travel_agent.weather.providers.qweather import QWeatherProvider


@pytest.mark.asyncio
async def test_qweather_adapter_resolves_location_and_normalizes_daily_forecast() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/geo/v2/city/lookup":
            return httpx.Response(
                200,
                json={
                    "code": "200",
                    "location": [
                        {
                            "id": "101210101",
                            "name": "杭州",
                            "lat": "30.2741",
                            "lon": "120.1551",
                            "tz": "Asia/Shanghai",
                        }
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "code": "200",
                "days": [
                    {
                        "forecastStartTime": "2026-10-02T00:00+08:00",
                        "temperatureMax": {"value": "12"},
                        "temperatureMin": {"value": "20"},
                        "daytime": {
                            "condition": {"text": "雷阵雨"},
                            "wind": {"scale": "3-5"},
                        },
                        "nighttime": {
                            "condition": {"text": "多云"},
                            "wind": {"scale": "2"},
                        },
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = QWeatherProvider(
            client, api_host="https://weather.example", token="jwt-secret"
        )
        location = await provider.resolve_location(" 杭州 ")
        forecast = await provider.get_forecast(
            location,
            start_date=date(2026, 10, 2),
            end_date=date(2026, 10, 2),
        )

    assert location.adcode == "101210101"
    assert len(forecast.days) == 1
    day = forecast.days[0]
    assert day.high_celsius == 20
    assert day.low_celsius == 12
    assert day.day_phenomenon is WeatherPhenomenon.THUNDERSTORM
    assert day.day_wind_level == 5
    assert requests[0].headers["Authorization"] == "Bearer jwt-secret"
    assert requests[1].url.path == "/weather/v1/daily/30.2741/120.1551"


@pytest.mark.asyncio
async def test_qweather_auth_failure_is_permanent_and_safe() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"code": "401", "secret": "hidden"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = QWeatherProvider(
            client, api_host="https://weather.example", token="bad-token"
        )
        with pytest.raises(ToolProviderError) as raised:
            await provider.resolve_location("杭州")

    assert raised.value.category is ToolErrorCategory.AUTHENTICATION
    assert raised.value.retryable is False
    assert "hidden" not in raised.value.safe_message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "category", "retryable"),
    [
        (403, ToolErrorCategory.AUTHENTICATION, False),
        (429, ToolErrorCategory.RATE_LIMIT, True),
        (503, ToolErrorCategory.UPSTREAM_UNAVAILABLE, True),
    ],
)
async def test_qweather_classifies_http_failures(status, category, retryable) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"code": str(status)})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = QWeatherProvider(
            client, api_host="https://weather.example", token="token"
        )
        with pytest.raises(ToolProviderError) as raised:
            await provider.resolve_location("杭州")
    assert raised.value.category is category
    assert raised.value.retryable is retryable


@pytest.mark.asyncio
async def test_qweather_rejects_invalid_location_and_provider_code() -> None:
    responses = iter(
        [
            httpx.Response(200, json={"code": "200", "location": []}),
            httpx.Response(200, json={"code": "500"}),
        ]
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return next(responses)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = QWeatherProvider(
            client, api_host="https://weather.example", token="token"
        )
        with pytest.raises(ToolProviderError) as unresolved:
            await provider.resolve_location("未知城市")
        with pytest.raises(ToolProviderError) as upstream:
            await provider.resolve_location("杭州")
    assert unresolved.value.code == "weather_location_unresolved"
    assert upstream.value.category is ToolErrorCategory.UPSTREAM_UNAVAILABLE
