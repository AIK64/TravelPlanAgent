from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class WeatherAvailability(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


class WeatherPhenomenon(StrEnum):
    CLEAR = "clear"
    CLOUDY = "cloudy"
    RAIN = "rain"
    HEAVY_RAIN = "heavy_rain"
    THUNDERSTORM = "thunderstorm"
    SNOW = "snow"
    ICE = "ice"
    FOG = "fog"
    DUST = "dust"
    UNKNOWN = "unknown"


class WeatherRiskKind(StrEnum):
    PRECIPITATION = "precipitation"
    THUNDERSTORM = "thunderstorm"
    SNOW_OR_ICE = "snow_or_ice"
    EXTREME_HEAT = "extreme_heat"
    EXTREME_COLD = "extreme_cold"
    STRONG_WIND = "strong_wind"
    LOW_VISIBILITY = "low_visibility"
    UNKNOWN = "unknown"


class WeatherRiskLevel(StrEnum):
    NORMAL = "normal"
    WARNING = "warning"
    SEVERE = "severe"
    UNKNOWN = "unknown"


class ChangeEventKind(StrEnum):
    WEATHER_ALERT = "weather_alert"
    WEATHER_RISK_CHANGED = "weather_risk_changed"
    WEATHER_RECOVERED = "weather_recovered"


class ExposureKind(StrEnum):
    INDOOR = "indoor"
    OUTDOOR = "outdoor"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class WeatherEventStatus(StrEnum):
    OBSERVED = "observed"
    NO_PLAN_IMPACT = "no_plan_impact"
    NEEDS_USER_ATTENTION = "needs_user_attention"
    PREVIEW_CREATED = "preview_created"
    APPROVED = "approved"
    REJECTED = "rejected"
    DISMISSED = "dismissed"


class WeatherRefreshOutcome(StrEnum):
    NO_CHANGE = "no_change"
    DUPLICATE = "duplicate"
    RECOVERED = "recovered"
    NO_PLAN_IMPACT = "no_plan_impact"
    NEEDS_USER_ATTENTION = "needs_user_attention"
    PREVIEW_CREATED = "preview_created"
    PROVIDER_FAILED = "provider_failed"


class WeatherLocation(BaseModel):
    model_config = ConfigDict(frozen=True)

    city_name: str = Field(min_length=1, max_length=100)
    adcode: str = Field(pattern=r"^\d{6}$")
    timezone: str = "Asia/Shanghai"
    provider: str


class DailyWeather(BaseModel):
    model_config = ConfigDict(frozen=True)

    date: date
    day_phenomenon: WeatherPhenomenon
    night_phenomenon: WeatherPhenomenon
    high_celsius: int | None = Field(default=None, ge=-80, le=80)
    low_celsius: int | None = Field(default=None, ge=-80, le=80)
    day_wind_level: int | None = Field(default=None, ge=0, le=17)
    night_wind_level: int | None = Field(default=None, ge=0, le=17)

    @model_validator(mode="after")
    def validate_temperature_order(self) -> "DailyWeather":
        if (
            self.high_celsius is not None
            and self.low_celsius is not None
            and self.high_celsius < self.low_celsius
        ):
            raise ValueError("high_celsius must not be lower than low_celsius")
        return self


class WeatherForecast(BaseModel):
    """Provider 标准化输出；不包含供应商原始响应。"""

    model_config = ConfigDict(frozen=True)

    location: WeatherLocation
    provider: str
    provider_reported_at: datetime | None = None
    days: tuple[DailyWeather, ...] = Field(max_length=10)


class WeatherSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    snapshot_id: str
    location: WeatherLocation
    provider: str
    provider_reported_at: datetime | None = None
    fetched_at: datetime
    expires_at: datetime
    days: tuple[DailyWeather, ...]
    snapshot_fingerprint: str


class DailyWeatherRisk(BaseModel):
    model_config = ConfigDict(frozen=True)

    date: date
    level: WeatherRiskLevel
    kinds: tuple[WeatherRiskKind, ...]
    evidence_codes: tuple[str, ...]
    policy_version: str
    risk_fingerprint: str


class ChangeEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: str
    event_fingerprint: str
    kind: ChangeEventKind
    session_id: str
    base_version_id: str
    previous_snapshot_id: str | None = None
    current_snapshot_id: str
    affected_dates: tuple[date, ...]
    before_risk_fingerprints: tuple[str, ...]
    after_risk_fingerprints: tuple[str, ...]
    created_at: datetime


class ActivityExposure(BaseModel):
    model_config = ConfigDict(frozen=True)

    item_id: str
    exposure: ExposureKind
    rule_id: str | None = None
    confidence: float = Field(ge=0, le=1)


class WeatherImpactResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: str
    affected_dates: tuple[date, ...] = ()
    affected_item_ids: tuple[str, ...] = ()
    preserved_dates: tuple[date, ...] = ()
    lock_conflicts: tuple[str, ...] = ()
    unknown_exposure_item_ids: tuple[str, ...] = ()
    requires_user_attention: bool = False
    reasons: tuple[str, ...] = ()


class WeatherRepairActionKind(StrEnum):
    REPLACE_WITH_INDOOR = "replace_with_indoor"
    MOVE_TO_SAFE_DATE = "move_to_safe_date"
    REMOVE_OPTIONAL_ITEM = "remove_optional_item"


class WeatherRepairAction(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: WeatherRepairActionKind
    item_id: str
    target_date: date | None = None
    replacement_poi_id: str | None = None
    replacement_poi_name: str | None = None
    evidence_codes: tuple[str, ...] = ()


class WeatherRepairPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: str
    base_version_id: str
    affected_dates: tuple[date, ...]
    actions: tuple[WeatherRepairAction, ...] = Field(min_length=1, max_length=3)
    required_tool_operations: tuple[str, ...]
    preserved_day_fingerprints: dict[str, str]


class WeatherEventReceipt(BaseModel):
    event_id: str
    event_fingerprint: str
    status: WeatherEventStatus
    resulting_preview_id: str | None = None
    resulting_version_id: str | None = None


class WeatherMonitorState(BaseModel):
    location: WeatherLocation | None = None
    availability: WeatherAvailability = WeatherAvailability.UNAVAILABLE
    latest_snapshot_id: str | None = None
    previous_snapshot_id: str | None = None
    latest_event_id: str | None = None
    attention_event_id: str | None = None
    last_outcome: WeatherRefreshOutcome | None = None
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_safe_error_code: str | None = None
    uncovered_dates: tuple[date, ...] = ()
    event_receipts: dict[str, WeatherEventReceipt] = Field(default_factory=dict)


class WeatherStateView(BaseModel):
    monitor: WeatherMonitorState
    latest_snapshot: WeatherSnapshot | None = None
    latest_risks: tuple[DailyWeatherRisk, ...] = ()
    latest_event: ChangeEvent | None = None


class WeatherEventView(BaseModel):
    event: ChangeEvent
    receipt: WeatherEventReceipt | None = None


class WeatherRefreshRequest(BaseModel):
    request_id: UUID
    expected_active_version_id: str | None = None
    expected_session_revision: int = Field(ge=0)


class RefreshWeatherAction(BaseModel):
    kind: Literal["refresh_weather"] = "refresh_weather"


class DismissWeatherEventAction(BaseModel):
    kind: Literal["dismiss_weather_event"] = "dismiss_weather_event"
    event_id: str = Field(min_length=1, max_length=256)
