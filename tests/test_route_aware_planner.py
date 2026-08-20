from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from travel_agent.domain.models import (
    PlanStyle,
    PlanningAssumption,
    PlanningPOI,
    TimeWindow,
)
from travel_agent.domain.tool_models import (
    POIFacts,
    RouteMode,
    RouteResult,
    ValueSource,
    route_key,
)
from travel_agent.planning.drafts import (
    CandidateDraft,
    DraftDay,
    MissingPlanningPOI,
    collect_route_queries,
    prepare_candidate_drafts,
)
from travel_agent.planning.planner import MissingRouteResult, materialize_candidates


@pytest.fixture
def planning_pois(hangzhou_trip) -> list[PlanningPOI]:
    poi_specs = [
        ("lingyin", "灵隐寺", 120.1017, 30.2404, ["人文", "寺庙"], "75", 120),
        ("west_lake", "西湖", 120.1487, 30.2448, ["自然"], "0", 120),
        ("hefang", "河坊街", 120.1714, 30.2371, ["美食", "人文"], "60", 90),
        ("museum", "浙江省博物馆", 120.1420, 30.2537, ["人文", "博物馆"], "0", 90),
        ("wetland", "西溪湿地", 120.0624, 30.2668, ["自然"], "80", 180),
    ]
    trip_dates = [
        hangzhou_trip.start_date + timedelta(days=index)
        for index in range(hangzhou_trip.day_count)
    ]
    window = TimeWindow(start="08:00", end="21:00")
    return [
        PlanningPOI(
            facts=POIFacts(
                id=poi_id,
                name=name,
                city="杭州",
                coordinate={"longitude": longitude, "latitude": latitude},
                categories=categories,
                provider="fixture",
                fetched_at=hangzhou_trip.arrival.at,
                data_confidence=0.9,
            ),
            opening_windows={trip_date: window for trip_date in trip_dates},
            duration_minutes=duration,
            party_cost=Decimal(cost),
            data_confidence=0.9,
        )
        for poi_id, name, longitude, latitude, categories, cost, duration in poi_specs
    ]


@pytest.fixture
def candidate_drafts(hangzhou_trip, planning_pois) -> list[CandidateDraft]:
    return prepare_candidate_drafts(hangzhou_trip, planning_pois, replan_round=0)


@pytest.fixture
def single_draft(hangzhou_trip, planning_pois) -> CandidateDraft:
    return CandidateDraft(
        id="single",
        style=PlanStyle.RELAXED,
        days=tuple(
            DraftDay(
                date=hangzhou_trip.start_date + timedelta(days=index),
                poi_ids=(planning_pois[0].facts.id,) if index == 0 else (),
            )
            for index in range(hangzhou_trip.day_count)
        ),
    )


def _route_results(hangzhou_trip, queries, *, confidence=0.9):
    return {
        route_key(query): RouteResult(
            distance_meters=4200 + index * 100,
            duration_minutes=18 + index,
            provider="fixture",
            data_confidence=confidence,
            fetched_at=hangzhou_trip.arrival.at,
        )
        for index, query in enumerate(queries)
    }


def _forbid_route_estimate(*_args):
    raise AssertionError("route estimate used")


def test_drafts_use_haversine_only_for_ordering(
    hangzhou_trip, planning_pois, monkeypatch
):
    monkeypatch.setattr(
        "travel_agent.planning.routing.estimate_route",
        _forbid_route_estimate,
    )
    drafts = prepare_candidate_drafts(hangzhou_trip, planning_pois, replan_round=0)

    assert {draft.style for draft in drafts} == set(PlanStyle)
    assert all(len(draft.days) == hangzhou_trip.day_count for draft in drafts)
    assert all(
        set(draft.model_dump()) == {"id", "style", "days"}
        for draft in drafts
    )
    assert all(
        set(day.model_dump()) == {"date", "poi_ids"}
        for draft in drafts
        for day in draft.days
    )


def test_replan_density_is_bounded_and_keeps_must_visit(
    hangzhou_trip, planning_pois
):
    drafts = prepare_candidate_drafts(
        hangzhou_trip, planning_pois, replan_round=5
    )

    assert all(
        sum(len(day.poi_ids) for day in draft.days) == hangzhou_trip.day_count
        for draft in drafts
    )
    assert all(
        "lingyin" in {poi_id for day in draft.days for poi_id in day.poi_ids}
        for draft in drafts
    )


def test_candidate_drafts_are_immutable(hangzhou_trip):
    day = DraftDay(date=hangzhou_trip.start_date, poi_ids=("lingyin",))
    draft = CandidateDraft(id="relaxed-r0", style=PlanStyle.RELAXED, days=(day,))

    with pytest.raises(ValidationError, match="frozen"):
        draft.id = "changed"


def test_route_queries_are_directional_and_deduplicated(
    hangzhou_trip, candidate_drafts, planning_pois
):
    queries = collect_route_queries(hangzhou_trip, candidate_drafts, planning_pois)
    keys = [route_key(query) for query in queries]

    assert len(keys) == len(set(keys))
    assert all(query.mode is RouteMode.DRIVING for query in queries)
    assert all(query.strategy == 32 for query in queries)
    assert all(query.destination_poi_id is not None for query in queries)


def test_route_queries_keep_opposite_directions(hangzhou_trip, planning_pois):
    first, second = planning_pois[:2]
    drafts = [
        CandidateDraft(
            id="directional",
            style=PlanStyle.RELAXED,
            days=(
                DraftDay(
                    date=hangzhou_trip.start_date,
                    poi_ids=(first.facts.id, second.facts.id),
                ),
                DraftDay(
                    date=hangzhou_trip.start_date + timedelta(days=1),
                    poi_ids=(second.facts.id, first.facts.id),
                ),
            ),
        )
    ]

    queries = collect_route_queries(hangzhou_trip, drafts, planning_pois)
    poi_pairs = [
        (query.origin_poi_id, query.destination_poi_id)
        for query in queries
        if query.origin_poi_id is not None
    ]

    assert (first.facts.id, second.facts.id) in poi_pairs
    assert (second.facts.id, first.facts.id) in poi_pairs


def test_route_queries_reject_unknown_draft_poi_id(hangzhou_trip, planning_pois):
    draft = CandidateDraft(
        id="broken",
        style=PlanStyle.RELAXED,
        days=(DraftDay(date=hangzhou_trip.start_date, poi_ids=("missing",)),),
    )

    with pytest.raises(MissingPlanningPOI, match="missing"):
        collect_route_queries(hangzhou_trip, [draft], planning_pois)


def test_materialization_uses_provider_route_values(
    hangzhou_trip, single_draft, planning_pois, monkeypatch
):
    monkeypatch.setattr(
        "travel_agent.planning.planner.estimate_route",
        _forbid_route_estimate,
    )
    query = collect_route_queries(
        hangzhou_trip, [single_draft], planning_pois
    )[0]
    routes = {
        route_key(query): RouteResult(
            distance_meters=4200,
            duration_minutes=18,
            provider="fixture",
            data_confidence=0.9,
            fetched_at=hangzhou_trip.arrival.at,
        )
    }

    candidate = materialize_candidates(
        hangzhou_trip, [single_draft], planning_pois, routes
    )[0]

    first = candidate.days[0].items[0]
    assert first.distance_from_previous_meters == 4200
    assert first.travel_from_previous_minutes == 18
    assert first.walking_distance_estimated is True
    assert candidate.days[0].walking_distance_meters == 504


def test_materialization_rejects_missing_route_result(
    hangzhou_trip, single_draft, planning_pois
):
    query = collect_route_queries(
        hangzhou_trip, [single_draft], planning_pois
    )[0]

    with pytest.raises(MissingRouteResult) as error:
        materialize_candidates(
            hangzhou_trip, [single_draft], planning_pois, routes={}
        )

    assert error.value.route_key == route_key(query)


def test_materialization_checks_all_draft_segments_before_scheduling(
    hangzhou_trip, planning_pois
):
    first_date = hangzhou_trip.start_date
    closes_before_arrival = planning_pois[1].model_copy(
        update={
            "opening_windows": {
                **planning_pois[1].opening_windows,
                first_date: TimeWindow(start="09:00", end="11:00"),
            }
        }
    )
    second = planning_pois[2]
    pois = [planning_pois[0], closes_before_arrival, second, *planning_pois[3:]]
    draft = CandidateDraft(
        id="preflight",
        style=PlanStyle.RELAXED,
        days=(
            DraftDay(
                date=first_date,
                poi_ids=(closes_before_arrival.facts.id, second.facts.id),
            ),
        ),
    )
    queries = collect_route_queries(hangzhou_trip, [draft], pois)
    only_first_route = _route_results(hangzhou_trip, queries[:1])

    with pytest.raises(MissingRouteResult) as error:
        materialize_candidates(
            hangzhou_trip, [draft], pois, only_first_route
        )

    assert error.value.route_key == route_key(queries[1])


def test_materialization_preserves_assumptions_and_marks_derived_walking_once(
    hangzhou_trip, single_draft, planning_pois
):
    upstream = PlanningAssumption(
        field="duration_minutes",
        value="120",
        reason="Provider 未返回游览时长",
        source=ValueSource.DEFAULT,
        affected_dates=[hangzhou_trip.start_date],
        created_at=hangzhou_trip.arrival.at,
    )
    pois = [
        planning_pois[0].model_copy(update={"assumptions": [upstream]}),
        *planning_pois[1:],
    ]
    queries = collect_route_queries(hangzhou_trip, [single_draft], pois)

    candidate = materialize_candidates(
        hangzhou_trip,
        [single_draft],
        pois,
        _route_results(hangzhou_trip, queries),
    )[0]

    assert upstream in candidate.assumptions
    walking_assumptions = [
        assumption
        for assumption in candidate.assumptions
        if assumption.field == "walking_distance"
    ]
    assert len(walking_assumptions) == 1
    assert walking_assumptions[0].source is ValueSource.DEFAULT
    assert "驾车距离" in walking_assumptions[0].reason


def test_materialization_uses_daily_window_and_tracks_unknown_party_cost(
    hangzhou_trip, planning_pois
):
    first_date = hangzhou_trip.start_date
    first = planning_pois[0].model_copy(
        update={
            "opening_windows": {
                **planning_pois[0].opening_windows,
                first_date: TimeWindow(start="15:00", end="18:00"),
            }
        }
    )
    second = planning_pois[1].model_copy(update={"party_cost": None})
    pois = [first, second, *planning_pois[2:]]
    draft = CandidateDraft(
        id="cost-and-window",
        style=PlanStyle.BALANCED,
        days=(DraftDay(date=first_date, poi_ids=(first.facts.id, second.facts.id)),),
    )
    queries = collect_route_queries(hangzhou_trip, [draft], pois)

    candidate = materialize_candidates(
        hangzhou_trip,
        [draft],
        pois,
        _route_results(hangzhou_trip, queries),
    )[0]

    day = candidate.days[0]
    assert day.items[0].start_at.hour == 15
    assert day.known_estimated_cost == first.party_cost
    assert day.unknown_cost_item_count == 1
    assert candidate.metrics.known_estimated_cost == first.party_cost
    assert candidate.metrics.unknown_cost_item_count == 1


def test_route_confidence_changes_candidate_confidence_and_score(
    hangzhou_trip, single_draft, planning_pois
):
    queries = collect_route_queries(
        hangzhou_trip, [single_draft], planning_pois
    )
    high = materialize_candidates(
        hangzhou_trip,
        [single_draft],
        planning_pois,
        _route_results(hangzhou_trip, queries, confidence=0.9),
    )[0]
    low = materialize_candidates(
        hangzhou_trip,
        [single_draft],
        planning_pois,
        _route_results(hangzhou_trip, queries, confidence=0.1),
    )[0]

    assert high.metrics.data_confidence == 0.9
    assert low.metrics.data_confidence == 0.5
    assert high.score > low.score


def test_default_assumption_increases_warning_risk(
    hangzhou_trip, single_draft, planning_pois
):
    queries = collect_route_queries(
        hangzhou_trip, [single_draft], planning_pois
    )
    routes = _route_results(hangzhou_trip, queries)
    baseline = materialize_candidates(
        hangzhou_trip, [single_draft], planning_pois, routes
    )[0]
    duration_assumption = PlanningAssumption(
        field="duration_minutes",
        value="120",
        reason="Provider 未返回游览时长",
        source=ValueSource.DEFAULT,
        affected_dates=[hangzhou_trip.start_date],
        created_at=hangzhou_trip.arrival.at,
    )
    assumed_pois = [
        planning_pois[0].model_copy(
            update={"assumptions": [duration_assumption]}
        ),
        *planning_pois[1:],
    ]
    warned = materialize_candidates(
        hangzhou_trip, [single_draft], assumed_pois, routes
    )[0]

    assert warned.metrics == baseline.metrics
    assert warned.score < baseline.score
