from __future__ import annotations

from hashlib import sha256
import json
import re

from travel_agent.domain.weather_models import (
    DailyWeather,
    DailyWeatherRisk,
    WeatherPhenomenon,
    WeatherRiskKind,
    WeatherRiskLevel,
)


WEATHER_POLICY_VERSION = "weather-risk-v1"


def normalize_phenomenon(value: object) -> WeatherPhenomenon:
    text = str(value or "").strip()
    if not text:
        return WeatherPhenomenon.UNKNOWN
    if "暴雨" in text or "大暴雨" in text or "特大暴雨" in text:
        return WeatherPhenomenon.HEAVY_RAIN
    if "雷" in text or "冰雹" in text:
        return WeatherPhenomenon.THUNDERSTORM
    if "冻雨" in text or "冰" in text:
        return WeatherPhenomenon.ICE
    if "雪" in text:
        return WeatherPhenomenon.SNOW
    if "雨" in text:
        return WeatherPhenomenon.RAIN
    if "沙" in text or "尘" in text:
        return WeatherPhenomenon.DUST
    if "雾" in text or "霾" in text:
        return WeatherPhenomenon.FOG
    if "晴" in text:
        return WeatherPhenomenon.CLEAR
    if "云" in text or "阴" in text:
        return WeatherPhenomenon.CLOUDY
    return WeatherPhenomenon.UNKNOWN


def parse_temperature(value: object) -> int | None:
    text = str(value or "").strip().replace("℃", "")
    if not text:
        return None
    try:
        parsed = int(float(text))
    except ValueError:
        return None
    return parsed if -80 <= parsed <= 80 else None


def parse_wind_level(value: object) -> int | None:
    numbers = [int(item) for item in re.findall(r"\d+", str(value or ""))]
    if not numbers:
        return None
    parsed = max(numbers)
    return parsed if 0 <= parsed <= 17 else None


def _fingerprint(payload: object) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def classify_daily_weather(day: DailyWeather) -> DailyWeatherRisk:
    kinds: set[WeatherRiskKind] = set()
    evidence: set[str] = set()
    levels: list[WeatherRiskLevel] = []
    phenomena = {day.day_phenomenon, day.night_phenomenon}

    if WeatherPhenomenon.HEAVY_RAIN in phenomena:
        kinds.add(WeatherRiskKind.PRECIPITATION)
        evidence.add("heavy_rain")
        levels.append(WeatherRiskLevel.SEVERE)
    if WeatherPhenomenon.THUNDERSTORM in phenomena:
        kinds.add(WeatherRiskKind.THUNDERSTORM)
        evidence.add("thunderstorm")
        levels.append(WeatherRiskLevel.SEVERE)
    if phenomena & {WeatherPhenomenon.SNOW, WeatherPhenomenon.ICE}:
        kinds.add(WeatherRiskKind.SNOW_OR_ICE)
        evidence.add("snow_or_ice")
        levels.append(WeatherRiskLevel.SEVERE)
    if WeatherPhenomenon.RAIN in phenomena:
        kinds.add(WeatherRiskKind.PRECIPITATION)
        evidence.add("rain")
        levels.append(WeatherRiskLevel.WARNING)
    if phenomena & {WeatherPhenomenon.FOG, WeatherPhenomenon.DUST}:
        kinds.add(WeatherRiskKind.LOW_VISIBILITY)
        evidence.add("low_visibility")
        levels.append(WeatherRiskLevel.WARNING)
    if day.high_celsius is not None and day.high_celsius >= 38:
        kinds.add(WeatherRiskKind.EXTREME_HEAT)
        evidence.add("high_temp_ge_38")
        levels.append(WeatherRiskLevel.SEVERE)
    elif day.high_celsius is not None and day.high_celsius >= 35:
        kinds.add(WeatherRiskKind.EXTREME_HEAT)
        evidence.add("high_temp_ge_35")
        levels.append(WeatherRiskLevel.WARNING)
    if day.low_celsius is not None and day.low_celsius <= 0:
        kinds.add(WeatherRiskKind.EXTREME_COLD)
        evidence.add("low_temp_le_0")
        levels.append(WeatherRiskLevel.WARNING)
    wind = max(
        value
        for value in (day.day_wind_level, day.night_wind_level, 0)
        if value is not None
    )
    if wind >= 8:
        kinds.add(WeatherRiskKind.STRONG_WIND)
        evidence.add("wind_ge_8")
        levels.append(WeatherRiskLevel.SEVERE)
    elif wind >= 6:
        kinds.add(WeatherRiskKind.STRONG_WIND)
        evidence.add("wind_ge_6")
        levels.append(WeatherRiskLevel.WARNING)

    unknown = WeatherPhenomenon.UNKNOWN in phenomena
    if unknown:
        kinds.add(WeatherRiskKind.UNKNOWN)
        evidence.add("unknown_phenomenon")
    if WeatherRiskLevel.SEVERE in levels:
        level = WeatherRiskLevel.SEVERE
    elif WeatherRiskLevel.WARNING in levels:
        level = WeatherRiskLevel.WARNING
    elif unknown:
        level = WeatherRiskLevel.UNKNOWN
    else:
        level = WeatherRiskLevel.NORMAL

    ordered_kinds = tuple(sorted(kinds, key=lambda item: item.value))
    ordered_evidence = tuple(sorted(evidence))
    risk_fingerprint = _fingerprint(
        {
            "date": day.date.isoformat(),
            "level": level.value,
            "kinds": [item.value for item in ordered_kinds],
            "evidence": ordered_evidence,
            "policy": WEATHER_POLICY_VERSION,
        }
    )
    return DailyWeatherRisk(
        date=day.date,
        level=level,
        kinds=ordered_kinds,
        evidence_codes=ordered_evidence,
        policy_version=WEATHER_POLICY_VERSION,
        risk_fingerprint=risk_fingerprint,
    )


def classify_forecast(days: tuple[DailyWeather, ...]) -> tuple[DailyWeatherRisk, ...]:
    return tuple(classify_daily_weather(day) for day in sorted(days, key=lambda x: x.date))
