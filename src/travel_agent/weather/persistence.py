from __future__ import annotations

from datetime import datetime, timedelta, timezone

from travel_agent.domain.lifecycle_models import PlanSessionRecord
from travel_agent.domain.weather_models import (
    ChangeEvent,
    DailyWeatherRisk,
    WeatherAvailability,
    WeatherEventReceipt,
    WeatherEventStatus,
    WeatherRefreshOutcome,
    WeatherSnapshot,
    WeatherStateView,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def persist_weather_observation(
    session: PlanSessionRecord,
    *,
    snapshot: WeatherSnapshot,
    risks: tuple[DailyWeatherRisk, ...],
    outcome: WeatherRefreshOutcome,
    event: ChangeEvent | None = None,
    event_status: WeatherEventStatus | None = None,
    max_events: int = 50,
) -> None:
    monitor = session.weather_monitor
    if monitor.latest_snapshot_id != snapshot.snapshot_id:
        monitor.previous_snapshot_id = monitor.latest_snapshot_id
    monitor.latest_snapshot_id = snapshot.snapshot_id
    monitor.location = snapshot.location
    monitor.availability = WeatherAvailability.FRESH
    monitor.last_attempt_at = snapshot.fetched_at
    monitor.last_success_at = snapshot.fetched_at
    monitor.last_safe_error_code = None
    monitor.last_outcome = outcome

    session.weather_snapshots[snapshot.snapshot_id] = snapshot
    session.weather_risks[snapshot.snapshot_id] = risks
    retained = sorted(
        session.weather_snapshots.values(),
        key=lambda item: item.fetched_at,
        reverse=True,
    )[:2]
    retained_ids = {item.snapshot_id for item in retained}
    session.weather_snapshots = {
        key: value
        for key, value in session.weather_snapshots.items()
        if key in retained_ids
    }
    session.weather_risks = {
        key: value for key, value in session.weather_risks.items() if key in retained_ids
    }
    if monitor.previous_snapshot_id not in retained_ids:
        monitor.previous_snapshot_id = None

    if session.snapshot is not None:
        covered = {item.date for item in snapshot.days}
        trip = session.snapshot.trip
        monitor.uncovered_dates = tuple(
            day for day in _trip_days(trip.start_date, trip.end_date)
            if day not in covered
        )

    if event is not None:
        session.weather_events[event.event_id] = event
        monitor.latest_event_id = event.event_id
        if event_status is not None:
            monitor.event_receipts[event.event_id] = WeatherEventReceipt(
                event_id=event.event_id,
                event_fingerprint=event.event_fingerprint,
                status=event_status,
            )
            monitor.attention_event_id = (
                event.event_id
                if event_status is WeatherEventStatus.NEEDS_USER_ATTENTION
                else None
            )
    if len(session.weather_events) > max_events:
        ordered = sorted(
            session.weather_events.values(),
            key=lambda item: item.created_at,
            reverse=True,
        )[:max_events]
        keep = {item.event_id for item in ordered}
        session.weather_events = {
            key: value for key, value in session.weather_events.items() if key in keep
        }
        monitor.event_receipts = {
            key: value for key, value in monitor.event_receipts.items() if key in keep
        }


def mark_weather_failed(
    session: PlanSessionRecord,
    *,
    safe_error_code: str,
    at: datetime | None = None,
) -> None:
    monitor = session.weather_monitor
    monitor.last_attempt_at = at or utcnow()
    monitor.last_safe_error_code = safe_error_code
    monitor.last_outcome = WeatherRefreshOutcome.PROVIDER_FAILED
    monitor.availability = WeatherAvailability.UNAVAILABLE


def weather_state_view(
    session: PlanSessionRecord,
    *,
    stale_max_seconds: int = 21_600,
    now: datetime | None = None,
) -> WeatherStateView:
    resolved_now = now or utcnow()
    monitor = session.weather_monitor.model_copy(deep=True)
    latest = (
        session.weather_snapshots.get(monitor.latest_snapshot_id)
        if monitor.latest_snapshot_id
        else None
    )
    if latest is not None:
        if monitor.last_outcome is WeatherRefreshOutcome.PROVIDER_FAILED:
            monitor.availability = (
                WeatherAvailability.STALE
                if resolved_now
                <= latest.fetched_at + timedelta(seconds=stale_max_seconds)
                else WeatherAvailability.UNAVAILABLE
            )
        elif resolved_now <= latest.expires_at:
            monitor.availability = WeatherAvailability.FRESH
        elif resolved_now <= latest.fetched_at + timedelta(seconds=stale_max_seconds):
            monitor.availability = WeatherAvailability.STALE
        else:
            monitor.availability = WeatherAvailability.UNAVAILABLE
    risks = (
        session.weather_risks.get(latest.snapshot_id, ()) if latest is not None else ()
    )
    event = (
        session.weather_events.get(monitor.latest_event_id)
        if monitor.latest_event_id
        else None
    )
    return WeatherStateView(
        monitor=monitor,
        latest_snapshot=latest,
        latest_risks=risks,
        latest_event=event,
    )


def find_event_by_fingerprint(
    session: PlanSessionRecord, fingerprint: str
) -> ChangeEvent | None:
    return next(
        (
            event
            for event in session.weather_events.values()
            if event.event_fingerprint == fingerprint
        ),
        None,
    )


def _trip_days(start, end):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)
