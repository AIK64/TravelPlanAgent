import asyncio
from datetime import date, datetime, timezone

import pytest

from travel_agent.domain.tool_models import (
    ToolCallContext,
    ToolErrorCategory,
    ToolStatus,
)
from travel_agent.domain.weather_models import (
    DailyWeather,
    WeatherForecast,
    WeatherLocation,
    WeatherPhenomenon,
)
from travel_agent.tools.cache import AsyncTTLCache
from travel_agent.tools.errors import ToolProviderError
from travel_agent.tools.retry import RetryPolicy
from travel_agent.weather.gateway import WeatherToolGateway
from travel_agent.weather.providers.amap import AMapWeatherProvider
from travel_agent.weather.providers.mock import MockWeatherProvider


class FakeAMapClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def request_json(self, operation, path, params):
        self.calls.append((operation, path, params))
        return self.responses[path]


@pytest.mark.asyncio
async def test_amap_location_resolution_uses_exact_normalized_city():
    client = FakeAMapClient(
        {
            "/v3/config/district": {
                "districts": [
                    {"name": "杭州市", "adcode": "330100"},
                    {"name": "杭州湾新区", "adcode": "330282"},
                ]
            }
        }
    )
    location = await AMapWeatherProvider(client).resolve_location(" 杭州 ")
    assert location.city_name == "杭州市"
    assert location.adcode == "330100"
    assert client.calls == [
        (
            "weather.resolve_location",
            "/v3/config/district",
            {
                "keywords": "杭州",
                "subdistrict": 0,
                "extensions": "base",
                "output": "JSON",
            },
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "code", "category"),
    [
        (
            {
                "districts": [
                    {"name": "杭州市", "adcode": "330100"},
                    {"name": "杭州区", "adcode": "330101"},
                ]
            },
            "weather_location_unresolved",
            ToolErrorCategory.INVALID_REQUEST,
        ),
        (
            {"districts": "invalid"},
            "invalid_district_response",
            ToolErrorCategory.INVALID_RESPONSE,
        ),
        (
            {"districts": [{"name": "", "adcode": "330100"}]},
            "invalid_district_response",
            ToolErrorCategory.INVALID_RESPONSE,
        ),
    ],
)
async def test_amap_location_resolution_rejects_unsafe_results(
    payload, code, category
):
    provider = AMapWeatherProvider(FakeAMapClient({"/v3/config/district": payload}))
    with pytest.raises(ToolProviderError) as raised:
        await provider.resolve_location("杭州")
    assert raised.value.code == code
    assert raised.value.category is category
    assert raised.value.retryable is False


@pytest.mark.asyncio
async def test_amap_forecast_normalizes_and_filters_provider_fields():
    client = FakeAMapClient(
        {
            "/v3/weather/weatherInfo": {
                "forecasts": [
                    {
                        "reporttime": "2026-10-01 08:30:00",
                        "casts": [
                            {"date": "bad"},
                            "bad-row",
                            {
                                "date": "2026-10-01",
                                "dayweather": "晴",
                                "nightweather": "晴",
                            },
                            {
                                "date": "2026-10-02",
                                "dayweather": "雷阵雨",
                                "nightweather": "阴",
                                "daytemp": "12℃",
                                "nighttemp": "20",
                                "daypower": "3-5",
                                "nightpower": "未知",
                            },
                            {
                                "date": "2026-10-03",
                                "dayweather": "晴",
                                "nightweather": "多云",
                                "daytemp": "30",
                                "nighttemp": "21",
                                "daypower": "2",
                                "nightpower": "3",
                            },
                        ],
                    }
                ]
            }
        }
    )
    location = WeatherLocation(
        city_name="杭州市", adcode="330100", provider="amap"
    )
    forecast = await AMapWeatherProvider(client).get_forecast(
        location,
        start_date=date(2026, 10, 2),
        end_date=date(2026, 10, 3),
    )
    assert [item.date for item in forecast.days] == [
        date(2026, 10, 2),
        date(2026, 10, 3),
    ]
    first = forecast.days[0]
    assert first.day_phenomenon is WeatherPhenomenon.THUNDERSTORM
    assert first.high_celsius == 20
    assert first.low_celsius == 12
    assert first.day_wind_level == 5
    assert first.night_wind_level is None
    assert forecast.provider_reported_at is not None
    assert forecast.provider_reported_at.utcoffset().total_seconds() == 8 * 3600
    assert client.calls[0][2]["city"] == "330100"
    assert "key" not in client.calls[0][2]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"forecasts": []},
        {"forecasts": ["invalid"]},
        {"forecasts": [{"casts": None}]},
    ],
)
async def test_amap_forecast_rejects_invalid_response_shapes(payload):
    provider = AMapWeatherProvider(
        FakeAMapClient({"/v3/weather/weatherInfo": payload})
    )
    location = WeatherLocation(
        city_name="杭州市", adcode="330100", provider="amap"
    )
    with pytest.raises(ToolProviderError) as raised:
        await provider.get_forecast(
            location,
            start_date=date(2026, 10, 2),
            end_date=date(2026, 10, 3),
        )
    assert raised.value.code == "invalid_weather_response"


@pytest.mark.asyncio
async def test_mock_weather_provider_has_deterministic_trip_relative_scenario():
    provider = MockWeatherProvider()
    location = await provider.resolve_location("杭州市")
    forecast = await provider.get_forecast(
        location,
        start_date=date(2026, 10, 2),
        end_date=date(2026, 10, 4),
    )
    assert location.adcode == "330100"
    assert len(forecast.days) == 3
    assert forecast.days[0].day_phenomenon is WeatherPhenomenon.RAIN
    assert forecast.days[1].day_phenomenon is WeatherPhenomenon.CLEAR
    with pytest.raises(ToolProviderError, match="无法唯一解析"):
        await provider.resolve_location("北京")


class CountingWeatherProvider:
    name = "counting"

    def __init__(self, *, transient_failures=0, permanent=False):
        self.location_calls = 0
        self.forecast_calls = 0
        self.transient_failures = transient_failures
        self.permanent = permanent
        self.location = WeatherLocation(
            city_name="杭州市", adcode="330100", provider=self.name
        )

    async def resolve_location(self, destination):
        self.location_calls += 1
        return self.location

    async def get_forecast(self, location, *, start_date, end_date):
        self.forecast_calls += 1
        if self.permanent or self.forecast_calls <= self.transient_failures:
            raise ToolProviderError(
                category=ToolErrorCategory.UPSTREAM_UNAVAILABLE,
                code="weather_upstream_unavailable",
                operation="weather.get_forecast",
                retryable=not self.permanent,
                safe_message="天气服务暂时不可用",
            )
        return WeatherForecast(
            location=location,
            provider=self.name,
            days=(
                DailyWeather(
                    date=start_date,
                    day_phenomenon=WeatherPhenomenon.CLEAR,
                    night_phenomenon=WeatherPhenomenon.CLEAR,
                ),
            ),
        )


def _gateway(provider, *, attempts=2):
    return WeatherToolGateway(
        provider=provider,
        cache=AsyncTTLCache(max_entries=20),
        retry=RetryPolicy(
            max_attempts=attempts,
            base_delay_seconds=0,
            max_delay_seconds=0,
            jitter=lambda: 0,
        ),
        semaphore=asyncio.Semaphore(2),
        utcnow=lambda: datetime(2026, 9, 30, 8, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_weather_gateway_caches_normalized_location_and_forecast():
    provider = CountingWeatherProvider()
    gateway = _gateway(provider)
    context = ToolCallContext(thread_id="weather-cache")
    first_location = await gateway.resolve_location("杭州", context)
    second_location = await gateway.resolve_location(" 杭州 ", context)
    assert first_location.status is ToolStatus.SUCCESS
    assert first_location.cache_hit is False
    assert second_location.cache_hit is True
    assert second_location.attempt_count == 0
    assert provider.location_calls == 1

    first = await gateway.get_forecast(
        provider.location,
        start_date=date(2026, 10, 2),
        end_date=date(2026, 10, 4),
        context=context,
    )
    second = await gateway.get_forecast(
        provider.location,
        start_date=date(2026, 10, 2),
        end_date=date(2026, 10, 4),
        context=context,
    )
    assert first.status is ToolStatus.SUCCESS
    assert first.attempt_count == 1
    assert second.cache_hit is True
    assert provider.forecast_calls == 1


@pytest.mark.asyncio
async def test_weather_gateway_retries_transient_failure_and_returns_safe_failure():
    transient = CountingWeatherProvider(transient_failures=1)
    recovered = await _gateway(transient).get_forecast(
        transient.location,
        start_date=date(2026, 10, 2),
        end_date=date(2026, 10, 4),
        context=ToolCallContext(thread_id="weather-retry"),
    )
    assert recovered.status is ToolStatus.SUCCESS
    assert recovered.attempt_count == 2

    permanent = CountingWeatherProvider(permanent=True)
    failed = await _gateway(permanent, attempts=3).get_forecast(
        permanent.location,
        start_date=date(2026, 10, 2),
        end_date=date(2026, 10, 4),
        context=ToolCallContext(thread_id="weather-failed"),
    )
    assert failed.status is ToolStatus.FAILED
    assert failed.attempt_count == 1
    assert failed.error is not None
    assert failed.error.code == "weather_upstream_unavailable"
