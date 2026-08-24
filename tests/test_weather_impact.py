from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from travel_agent.domain.lifecycle_models import (
    LockKind,
    PlanLock,
    PlanningSnapshot,
    PlanSessionRecord,
    PlanSessionStatus,
)
from travel_agent.domain.models import PlanningRequest
from travel_agent.domain.weather_models import (
    ChangeEvent,
    ChangeEventKind,
    DailyWeather,
    ExposureKind,
    WeatherEventStatus,
    WeatherForecast,
    WeatherImpactResult,
    WeatherLocation,
    WeatherPhenomenon,
    WeatherRefreshOutcome,
    WeatherRepairActionKind,
    WeatherRiskLevel,
)
from travel_agent.graph.workflow import run_planning
from travel_agent.lifecycle.fingerprints import with_stable_item_ids
from travel_agent.weather.events import build_weather_snapshot
from travel_agent.weather.exposure import classify_planning_poi, indoor_alternatives
from travel_agent.weather.impact import analyze_weather_impact
from travel_agent.weather.persistence import (
    find_event_by_fingerprint,
    mark_weather_failed,
    persist_weather_observation,
    weather_state_view,
)
from travel_agent.weather.policy import classify_forecast
from travel_agent.weather.repair import (
    build_weather_repair_plan,
    repair_plan_to_edit_patch,
)


UTC = timezone.utc


async def _planning_context(workflow, trip):
    thread_id = f"weather-impact-{uuid4()}"
    response = await run_planning(
        workflow, PlanningRequest(trip=trip), thread_id=thread_id
    )
    state = await workflow.aget_state({"configurable": {"thread_id": thread_id}})
    candidate = with_stable_item_ids("weather-session", response.selected_plan)
    draft = next(
        item for item in state.values["candidate_drafts"] if item.id == candidate.id
    )
    return candidate, draft, tuple(state.values["planning_pois"]), state.values


def _risks(days, phenomenon=WeatherPhenomenon.RAIN):
    return classify_forecast(
        tuple(
            DailyWeather(
                date=day,
                day_phenomenon=phenomenon,
                night_phenomenon=phenomenon,
                high_celsius=28,
                low_celsius=20,
                day_wind_level=3,
                night_wind_level=3,
            )
            for day in days
        )
    )


def _event(days, *, suffix="1"):
    return ChangeEvent(
        event_id=f"event-{suffix}",
        event_fingerprint=f"fingerprint-{suffix}",
        kind=ChangeEventKind.WEATHER_ALERT,
        session_id="weather-session",
        base_version_id="V1",
        current_snapshot_id=f"snapshot-{suffix}",
        affected_dates=tuple(days),
        before_risk_fingerprints=tuple("missing" for _ in days),
        after_risk_fingerprints=tuple(f"risk-{index}" for index, _ in enumerate(days)),
        created_at=datetime(2026, 9, 30, 8, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_exposure_rules_and_indoor_alternative_filtering(
    mock_workflow, hangzhou_trip
):
    candidate, _draft, pois, _state = await _planning_context(
        mock_workflow, hangzhou_trip
    )
    by_id = {poi.facts.id: poi for poi in pois}
    assert classify_planning_poi(
        by_id["hz_west_lake"], item_id="outdoor"
    ).exposure is ExposureKind.OUTDOOR
    assert classify_planning_poi(
        by_id["hz_lingyin"], item_id="mixed"
    ).exposure is ExposureKind.MIXED
    assert classify_planning_poi(
        by_id["hz_zhejiang_museum"], item_id="indoor"
    ).exposure is ExposureKind.INDOOR

    unknown = by_id["hz_zhejiang_museum"].model_copy(
        update={
            "facts": by_id["hz_zhejiang_museum"].facts.model_copy(
                update={"id": "unknown", "name": "普通餐厅", "categories": ["餐饮"]}
            )
        }
    )
    assert classify_planning_poi(unknown, item_id="unknown").exposure is ExposureKind.UNKNOWN

    used_ids = {
        item.poi_id
        for day in candidate.days
        for item in day.items
        if item.poi_id is not None
    }
    alternatives = indoor_alternatives(
        pois + (unknown,), excluded_poi_ids=used_ids, minimum_confidence=0.8
    )
    assert all(item.facts.id not in used_ids for item in alternatives)
    assert all(
        classify_planning_poi(item, item_id=item.facts.id).exposure
        is ExposureKind.INDOOR
        for item in alternatives
    )


@pytest.mark.asyncio
async def test_weather_impact_detects_outdoor_scope_locks_and_budgets(
    mock_workflow, hangzhou_trip
):
    candidate, _draft, pois, _state = await _planning_context(
        mock_workflow, hangzhou_trip
    )
    dates = tuple(day.date for day in candidate.days)
    first_item = candidate.days[0].items[0]
    locks = (
        PlanLock(
            lock_id=f"day:{dates[0].isoformat()}",
            kind=LockKind.DAY,
            target_id=dates[0].isoformat(),
            expected_fingerprint="day-fingerprint",
            created_by_request_id="request",
        ),
        PlanLock(
            lock_id=f"item:{first_item.item_id}",
            kind=LockKind.ITEM,
            target_id=first_item.item_id,
            expected_fingerprint="item-fingerprint",
            created_by_request_id="request",
        ),
    )
    impact = analyze_weather_impact(
        event=_event(dates),
        candidate=candidate,
        planning_pois=pois,
        risks=_risks(dates),
        locks=locks,
        max_affected_days=1,
    )
    assert impact.affected_item_ids
    assert dates[0] in impact.affected_dates
    assert impact.requires_user_attention is True
    assert f"day:{dates[0].isoformat()}" in impact.lock_conflicts
    assert "affected_day_budget_exceeded" in impact.reasons


@pytest.mark.asyncio
async def test_weather_impact_separates_unknown_data_from_no_plan_impact(
    mock_workflow, hangzhou_trip
):
    candidate, _draft, pois, _state = await _planning_context(
        mock_workflow, hangzhou_trip
    )
    first_date = candidate.days[0].date
    unknown = analyze_weather_impact(
        event=_event((first_date,), suffix="unknown"),
        candidate=candidate,
        planning_pois=pois,
        risks=_risks((first_date,), WeatherPhenomenon.UNKNOWN),
        locks=(),
    )
    assert unknown.requires_user_attention is True
    assert unknown.unknown_exposure_item_ids
    assert "weather_risk_unknown" in unknown.reasons

    missing_poi_id = candidate.days[0].items[0].poi_id
    missing = analyze_weather_impact(
        event=_event((first_date,), suffix="missing-poi"),
        candidate=candidate,
        planning_pois=tuple(item for item in pois if item.facts.id != missing_poi_id),
        risks=_risks((first_date,)),
        locks=(),
    )
    assert candidate.days[0].items[0].item_id in missing.unknown_exposure_item_ids
    assert "planning_poi_missing" in missing.reasons

    clear = analyze_weather_impact(
        event=_event((first_date,), suffix="clear"),
        candidate=candidate,
        planning_pois=pois,
        risks=_risks((first_date,), WeatherPhenomenon.CLEAR),
        locks=(),
    )
    assert clear.affected_item_ids == ()
    assert clear.requires_user_attention is False


@pytest.mark.asyncio
async def test_weather_repair_is_bounded_and_preserves_must_visit(
    mock_workflow, hangzhou_trip
):
    candidate, _draft, pois, _state = await _planning_context(
        mock_workflow, hangzhou_trip
    )
    all_items = [item for day in candidate.days for item in day.items]
    must = next(item for item in all_items if "灵隐寺" in item.name)
    optional = [item for item in all_items if item.item_id != must.item_id][:2]
    item_dates = {
        item.item_id: day.date for day in candidate.days for item in day.items
    }
    affected_ids = (must.item_id, optional[0].item_id, optional[1].item_id)
    affected_dates = tuple(sorted({item_dates[item_id] for item_id in affected_ids}))
    preserved_dates = tuple(
        day.date for day in candidate.days if day.date not in affected_dates
    )
    impact = WeatherImpactResult(
        event_id="event-repair",
        affected_dates=affected_dates,
        affected_item_ids=affected_ids,
        preserved_dates=preserved_dates,
    )
    indoor = next(item for item in pois if "博物馆" in item.facts.categories)
    risks = _risks(tuple(day.date for day in candidate.days), WeatherPhenomenon.CLEAR)
    plan = build_weather_repair_plan(
        event_id="event-repair",
        base_version_id="V1",
        trip=hangzhou_trip,
        candidate=candidate,
        impact=impact,
        risks=risks,
        alternatives=(indoor,),
        locks=(),
    )
    assert plan is not None
    assert len(plan.actions) == 3
    assert {action.kind for action in plan.actions} == {
        WeatherRepairActionKind.MOVE_TO_SAFE_DATE,
        WeatherRepairActionKind.REPLACE_WITH_INDOOR,
        WeatherRepairActionKind.REMOVE_OPTIONAL_ITEM,
    }
    must_action = next(action for action in plan.actions if action.item_id == must.item_id)
    assert must_action.kind is WeatherRepairActionKind.MOVE_TO_SAFE_DATE
    assert must_action.target_date != item_dates[must.item_id]
    patch = repair_plan_to_edit_patch(plan)
    assert {operation.kind.value for operation in patch.operations} == {
        "move_item",
        "replace_item",
        "remove_item",
    }
    assert all(operation.user_reason == "weather_event" for operation in patch.operations)

    impossible = build_weather_repair_plan(
        event_id="event-no-safe-day",
        base_version_id="V1",
        trip=hangzhou_trip,
        candidate=candidate,
        impact=WeatherImpactResult(
            event_id="event-no-safe-day",
            affected_dates=(item_dates[must.item_id],),
            affected_item_ids=(must.item_id,),
        ),
        risks=_risks(tuple(day.date for day in candidate.days)),
        alternatives=(),
        locks=(),
    )
    assert impossible is None
    missing = build_weather_repair_plan(
        event_id="event-missing",
        base_version_id="V1",
        trip=hangzhou_trip,
        candidate=candidate,
        impact=WeatherImpactResult(
            event_id="event-missing", affected_item_ids=("missing-item",)
        ),
        risks=risks,
        alternatives=(),
        locks=(),
    )
    assert missing is None


def _snapshot(day, fetched_at):
    location = WeatherLocation(
        city_name="杭州市", adcode="330100", provider="test"
    )
    return build_weather_snapshot(
        WeatherForecast(
            location=location,
            provider="test",
            days=(
                DailyWeather(
                    date=day,
                    day_phenomenon=WeatherPhenomenon.RAIN,
                    night_phenomenon=WeatherPhenomenon.CLEAR,
                ),
            ),
        ),
        fetched_at=fetched_at,
        expires_at=fetched_at + timedelta(minutes=30),
    )


@pytest.mark.asyncio
async def test_weather_observation_retention_freshness_and_failure_state(
    mock_workflow, hangzhou_trip
):
    candidate, draft, pois, state = await _planning_context(
        mock_workflow, hangzhou_trip
    )
    session = PlanSessionRecord(
        session_id="weather-session",
        lifecycle_thread_id="lifecycle:weather-session",
        status=PlanSessionStatus.ACTIVE,
        snapshot=PlanningSnapshot(
            trip=hangzhou_trip,
            candidates=(candidate,),
            recommended_candidate_id=candidate.id,
            candidate_drafts=(draft,),
            planning_pois=pois,
            route_results=dict(state["route_results"]),
        ),
    )
    base = datetime(2026, 9, 30, 8, tzinfo=UTC)
    snapshots = [
        _snapshot(hangzhou_trip.start_date + timedelta(days=index), base + timedelta(hours=index))
        for index in range(3)
    ]
    first_event = _event((hangzhou_trip.start_date,), suffix="retention-1")
    persist_weather_observation(
        session,
        snapshot=snapshots[0],
        risks=classify_forecast(snapshots[0].days),
        outcome=WeatherRefreshOutcome.NEEDS_USER_ATTENTION,
        event=first_event,
        event_status=WeatherEventStatus.NEEDS_USER_ATTENTION,
        max_events=1,
    )
    assert session.weather_monitor.attention_event_id == first_event.event_id
    assert len(session.weather_monitor.uncovered_dates) == 2
    assert find_event_by_fingerprint(session, first_event.event_fingerprint) == first_event

    second_event = _event(
        (hangzhou_trip.start_date + timedelta(days=1),), suffix="retention-2"
    ).model_copy(update={"created_at": base + timedelta(hours=1)})
    persist_weather_observation(
        session,
        snapshot=snapshots[1],
        risks=classify_forecast(snapshots[1].days),
        outcome=WeatherRefreshOutcome.NO_PLAN_IMPACT,
        event=second_event,
        event_status=WeatherEventStatus.NO_PLAN_IMPACT,
        max_events=1,
    )
    persist_weather_observation(
        session,
        snapshot=snapshots[2],
        risks=classify_forecast(snapshots[2].days),
        outcome=WeatherRefreshOutcome.NO_CHANGE,
        max_events=1,
    )
    assert set(session.weather_snapshots) == {
        snapshots[1].snapshot_id,
        snapshots[2].snapshot_id,
    }
    assert set(session.weather_events) == {second_event.event_id}
    assert session.weather_monitor.previous_snapshot_id == snapshots[1].snapshot_id

    fresh = weather_state_view(session, now=snapshots[2].expires_at)
    stale = weather_state_view(
        session, now=snapshots[2].expires_at + timedelta(seconds=1)
    )
    unavailable = weather_state_view(
        session,
        stale_max_seconds=60,
        now=snapshots[2].expires_at + timedelta(seconds=1),
    )
    assert fresh.monitor.availability.value == "fresh"
    assert stale.monitor.availability.value == "stale"
    assert unavailable.monitor.availability.value == "unavailable"

    mark_weather_failed(
        session,
        safe_error_code="weather_upstream_unavailable",
        at=snapshots[2].fetched_at + timedelta(hours=1),
    )
    assert session.weather_monitor.last_outcome is WeatherRefreshOutcome.PROVIDER_FAILED
    assert session.weather_monitor.last_safe_error_code == "weather_upstream_unavailable"
    assert session.weather_monitor.availability.value == "unavailable"
    failed_view = weather_state_view(
        session, now=snapshots[2].fetched_at + timedelta(hours=1)
    )
    assert failed_view.monitor.availability.value == "stale"
