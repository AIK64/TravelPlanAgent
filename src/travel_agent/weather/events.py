from __future__ import annotations

from datetime import date, datetime, timezone
from hashlib import sha256
import json

from travel_agent.domain.weather_models import (
    ChangeEvent,
    ChangeEventKind,
    DailyWeatherRisk,
    WeatherForecast,
    WeatherRiskLevel,
    WeatherSnapshot,
)


def _stable_hash(payload: object) -> str:
    return sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def build_weather_snapshot(
    forecast: WeatherForecast,
    *,
    fetched_at: datetime,
    expires_at: datetime,
) -> WeatherSnapshot:
    ordered = tuple(sorted(forecast.days, key=lambda item: item.date))
    fingerprint = _stable_hash(
        {
            "provider": forecast.provider,
            "adcode": forecast.location.adcode,
            "days": [item.model_dump(mode="json") for item in ordered],
        }
    )
    return WeatherSnapshot(
        snapshot_id=f"ws_{fingerprint[:16]}",
        location=forecast.location,
        provider=forecast.provider,
        provider_reported_at=forecast.provider_reported_at,
        fetched_at=fetched_at,
        expires_at=expires_at,
        days=ordered,
        snapshot_fingerprint=fingerprint,
    )


def derive_change_event(
    *,
    session_id: str,
    base_version_id: str,
    current_snapshot: WeatherSnapshot,
    current_risks: tuple[DailyWeatherRisk, ...],
    previous_snapshot: WeatherSnapshot | None,
    previous_risks: tuple[DailyWeatherRisk, ...],
    trip_dates: tuple[date, ...],
    now: datetime | None = None,
) -> ChangeEvent | None:
    current_by_date = {item.date: item for item in current_risks}
    previous_by_date = {item.date: item for item in previous_risks}
    affected: list[date] = []
    before: list[str] = []
    after: list[str] = []

    if previous_snapshot is None:
        for trip_date in trip_dates:
            risk = current_by_date.get(trip_date)
            if risk is None or risk.level is WeatherRiskLevel.NORMAL:
                continue
            affected.append(trip_date)
            before.append("missing")
            after.append(risk.risk_fingerprint)
        kind = ChangeEventKind.WEATHER_ALERT
    else:
        for trip_date in trip_dates:
            current = current_by_date.get(trip_date)
            previous = previous_by_date.get(trip_date)
            if current is None or previous is None:
                continue
            if current.risk_fingerprint == previous.risk_fingerprint:
                continue
            affected.append(trip_date)
            before.append(previous.risk_fingerprint)
            after.append(current.risk_fingerprint)
        if affected and all(
            current_by_date[item].level is WeatherRiskLevel.NORMAL
            for item in affected
        ):
            kind = ChangeEventKind.WEATHER_RECOVERED
        else:
            kind = ChangeEventKind.WEATHER_RISK_CHANGED

    if not affected:
        return None
    fingerprint = _stable_hash(
        {
            "session_id": session_id,
            "base_version_id": base_version_id,
            "kind": kind.value,
            "dates": [item.isoformat() for item in affected],
            "before": before,
            "after": after,
        }
    )
    return ChangeEvent(
        event_id=f"we_{fingerprint[:16]}",
        event_fingerprint=fingerprint,
        kind=kind,
        session_id=session_id,
        base_version_id=base_version_id,
        previous_snapshot_id=(
            previous_snapshot.snapshot_id if previous_snapshot is not None else None
        ),
        current_snapshot_id=current_snapshot.snapshot_id,
        affected_dates=tuple(affected),
        before_risk_fingerprints=tuple(before),
        after_risk_fingerprints=tuple(after),
        created_at=now or datetime.now(timezone.utc),
    )
