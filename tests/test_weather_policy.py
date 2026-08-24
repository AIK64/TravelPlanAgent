from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from travel_agent.domain.weather_models import (
    ChangeEventKind,
    DailyWeather,
    WeatherForecast,
    WeatherLocation,
    WeatherPhenomenon,
    WeatherRiskKind,
    WeatherRiskLevel,
)
from travel_agent.weather.events import build_weather_snapshot, derive_change_event
from travel_agent.weather.policy import (
    classify_daily_weather,
    classify_forecast,
    normalize_phenomenon,
    parse_temperature,
    parse_wind_level,
)


UTC = timezone.utc
TRIP_DATE = date(2026, 10, 2)
LOCATION = WeatherLocation(
    city_name="杭州市",
    adcode="330100",
    provider="test",
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("晴", WeatherPhenomenon.CLEAR),
        ("多云", WeatherPhenomenon.CLOUDY),
        ("阴", WeatherPhenomenon.CLOUDY),
        ("小雨", WeatherPhenomenon.RAIN),
        ("特大暴雨", WeatherPhenomenon.HEAVY_RAIN),
        ("雷阵雨", WeatherPhenomenon.THUNDERSTORM),
        ("冻雨", WeatherPhenomenon.ICE),
        ("大雪", WeatherPhenomenon.SNOW),
        ("雾霾", WeatherPhenomenon.FOG),
        ("扬沙", WeatherPhenomenon.DUST),
        ("", WeatherPhenomenon.UNKNOWN),
        (None, WeatherPhenomenon.UNKNOWN),
        ("火星天气", WeatherPhenomenon.UNKNOWN),
    ],
)
def test_normalize_weather_phenomenon(raw, expected):
    assert normalize_phenomenon(raw) is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("28℃", 28), ("-3", -3), ("35.9", 35), ("", None), ("bad", None), ("99", None)],
)
def test_parse_temperature_is_bounded(raw, expected):
    assert parse_temperature(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("3", 3), ("3-5", 5), ("≤4", 4), ("", None), ("bad", None), ("18", None)],
)
def test_parse_wind_level_is_conservative(raw, expected):
    assert parse_wind_level(raw) == expected


def _day(
    *,
    phenomenon: WeatherPhenomenon = WeatherPhenomenon.CLEAR,
    high: int | None = 28,
    low: int | None = 18,
    wind: int | None = 3,
) -> DailyWeather:
    return DailyWeather(
        date=TRIP_DATE,
        day_phenomenon=phenomenon,
        night_phenomenon=WeatherPhenomenon.CLEAR,
        high_celsius=high,
        low_celsius=low,
        day_wind_level=wind,
        night_wind_level=wind,
    )


@pytest.mark.parametrize(
    ("weather", "level", "kind", "evidence"),
    [
        (_day(), WeatherRiskLevel.NORMAL, None, None),
        (
            _day(phenomenon=WeatherPhenomenon.RAIN),
            WeatherRiskLevel.WARNING,
            WeatherRiskKind.PRECIPITATION,
            "rain",
        ),
        (
            _day(phenomenon=WeatherPhenomenon.HEAVY_RAIN),
            WeatherRiskLevel.SEVERE,
            WeatherRiskKind.PRECIPITATION,
            "heavy_rain",
        ),
        (
            _day(phenomenon=WeatherPhenomenon.THUNDERSTORM),
            WeatherRiskLevel.SEVERE,
            WeatherRiskKind.THUNDERSTORM,
            "thunderstorm",
        ),
        (
            _day(phenomenon=WeatherPhenomenon.SNOW),
            WeatherRiskLevel.SEVERE,
            WeatherRiskKind.SNOW_OR_ICE,
            "snow_or_ice",
        ),
        (
            _day(phenomenon=WeatherPhenomenon.FOG),
            WeatherRiskLevel.WARNING,
            WeatherRiskKind.LOW_VISIBILITY,
            "low_visibility",
        ),
        (
            _day(high=35),
            WeatherRiskLevel.WARNING,
            WeatherRiskKind.EXTREME_HEAT,
            "high_temp_ge_35",
        ),
        (
            _day(high=38),
            WeatherRiskLevel.SEVERE,
            WeatherRiskKind.EXTREME_HEAT,
            "high_temp_ge_38",
        ),
        (
            _day(low=0),
            WeatherRiskLevel.WARNING,
            WeatherRiskKind.EXTREME_COLD,
            "low_temp_le_0",
        ),
        (
            _day(wind=6),
            WeatherRiskLevel.WARNING,
            WeatherRiskKind.STRONG_WIND,
            "wind_ge_6",
        ),
        (
            _day(wind=8),
            WeatherRiskLevel.SEVERE,
            WeatherRiskKind.STRONG_WIND,
            "wind_ge_8",
        ),
        (
            _day(phenomenon=WeatherPhenomenon.UNKNOWN),
            WeatherRiskLevel.UNKNOWN,
            WeatherRiskKind.UNKNOWN,
            "unknown_phenomenon",
        ),
    ],
)
def test_weather_risk_policy(weather, level, kind, evidence):
    risk = classify_daily_weather(weather)
    assert risk.level is level
    if kind is None:
        assert risk.kinds == ()
    else:
        assert kind in risk.kinds
        assert evidence in risk.evidence_codes


def test_daily_weather_rejects_reversed_temperature_range():
    with pytest.raises(ValidationError, match="high_celsius"):
        _day(high=10, low=20)


def _forecast(day: DailyWeather, *, reported_at: datetime | None = None) -> WeatherForecast:
    return WeatherForecast(
        location=LOCATION,
        provider="test",
        provider_reported_at=reported_at,
        days=(day,),
    )


def _snapshot(day: DailyWeather, *, minute: int = 0, report_minute: int = 0):
    fetched_at = datetime(2026, 9, 30, 8, minute, tzinfo=UTC)
    return build_weather_snapshot(
        _forecast(
            day,
            reported_at=datetime(2026, 9, 30, 7, report_minute, tzinfo=UTC),
        ),
        fetched_at=fetched_at,
        expires_at=fetched_at + timedelta(minutes=30),
    )


def test_snapshot_fingerprint_ignores_fetch_and_provider_report_times():
    first = _snapshot(_day(phenomenon=WeatherPhenomenon.RAIN))
    second = _snapshot(
        _day(phenomenon=WeatherPhenomenon.RAIN), minute=10, report_minute=5
    )
    assert first.snapshot_id == second.snapshot_id
    assert first.snapshot_fingerprint == second.snapshot_fingerprint
    assert first.fetched_at != second.fetched_at


def test_change_event_lifecycle_alert_deduplicate_recovery_and_version_scope():
    rainy_snapshot = _snapshot(_day(phenomenon=WeatherPhenomenon.RAIN))
    rainy_risks = classify_forecast(rainy_snapshot.days)
    alert = derive_change_event(
        session_id="session-1",
        base_version_id="V1",
        current_snapshot=rainy_snapshot,
        current_risks=rainy_risks,
        previous_snapshot=None,
        previous_risks=(),
        trip_dates=(TRIP_DATE,),
        now=datetime(2026, 9, 30, 8, tzinfo=UTC),
    )
    assert alert is not None
    assert alert.kind is ChangeEventKind.WEATHER_ALERT
    assert alert.affected_dates == (TRIP_DATE,)

    duplicate = derive_change_event(
        session_id="session-1",
        base_version_id="V1",
        current_snapshot=rainy_snapshot,
        current_risks=rainy_risks,
        previous_snapshot=rainy_snapshot,
        previous_risks=rainy_risks,
        trip_dates=(TRIP_DATE,),
    )
    assert duplicate is None

    clear_snapshot = _snapshot(_day(), minute=20)
    clear_risks = classify_forecast(clear_snapshot.days)
    recovered = derive_change_event(
        session_id="session-1",
        base_version_id="V1",
        current_snapshot=clear_snapshot,
        current_risks=clear_risks,
        previous_snapshot=rainy_snapshot,
        previous_risks=rainy_risks,
        trip_dates=(TRIP_DATE,),
        now=datetime(2026, 9, 30, 8, 20, tzinfo=UTC),
    )
    assert recovered is not None
    assert recovered.kind is ChangeEventKind.WEATHER_RECOVERED

    rescoped = derive_change_event(
        session_id="session-1",
        base_version_id="V2",
        current_snapshot=rainy_snapshot,
        current_risks=rainy_risks,
        previous_snapshot=None,
        previous_risks=(),
        trip_dates=(TRIP_DATE,),
        now=datetime(2026, 9, 30, 8, tzinfo=UTC),
    )
    assert rescoped is not None
    assert rescoped.event_fingerprint != alert.event_fingerprint


def test_initial_normal_forecast_does_not_create_an_event():
    snapshot = _snapshot(_day())
    assert (
        derive_change_event(
            session_id="session-1",
            base_version_id="V1",
            current_snapshot=snapshot,
            current_risks=classify_forecast(snapshot.days),
            previous_snapshot=None,
            previous_risks=(),
            trip_dates=(TRIP_DATE,),
        )
        is None
    )
