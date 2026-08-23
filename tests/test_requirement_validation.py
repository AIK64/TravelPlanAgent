from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from travel_agent.domain.models import Coordinate
from travel_agent.requirements.models import (
    AnchorDraft,
    AnchorResolution,
    RequirementDraft,
    RequirementIssueCode,
)
from travel_agent.requirements.validation import (
    assemble_trip_spec,
    validate_requirement,
)


CHINA_TZ = timezone(timedelta(hours=8))


def _complete_draft() -> RequirementDraft:
    return RequirementDraft(
        destination="杭州",
        start_date=date(2026, 10, 2),
        end_date=date(2026, 10, 4),
        travelers=3,
        arrival=AnchorDraft(
            name="杭州东站",
            at=datetime(2026, 10, 2, 10, 30, tzinfo=CHINA_TZ),
        ),
        departure=AnchorDraft(
            name="杭州东站",
            at=datetime(2026, 10, 4, 19, 0, tzinfo=CHINA_TZ),
        ),
        accommodation_name="西湖东侧",
        total_budget=Decimal("1500"),
        interests=["自然", "美食"],
        must_visit=["灵隐寺"],
        daily_start=time(9, 0),
        daily_end=time(20, 0),
    )


def test_validation_reports_all_blocking_missing_fields_with_stable_questions():
    issues = validate_requirement(RequirementDraft(destination="杭州"))

    assert {issue.field for issue in issues} == {
        "start_date",
        "end_date",
        "arrival.name",
        "arrival.at",
        "departure.name",
        "departure.at",
    }
    assert all(issue.code is RequirementIssueCode.MISSING for issue in issues)
    assert all(issue.blocking for issue in issues)
    assert all(issue.question for issue in issues)


def test_validation_finds_conflicting_dates_without_calling_an_llm():
    draft = _complete_draft().model_copy(
        update={"end_date": date(2026, 10, 1)}
    )

    issues = validate_requirement(draft)

    assert any(
        issue.code is RequirementIssueCode.CONFLICT
        and issue.field == "date_range"
        for issue in issues
    )


def test_validation_checks_transport_dates_in_request_timezone():
    draft = _complete_draft().model_copy(
        update={
            "arrival": AnchorDraft(
                name="杭州东站",
                at=datetime(2026, 10, 1, 23, 0),
            ),
            "departure": AnchorDraft(
                name="杭州东站",
                at=datetime(2026, 10, 5, 1, 0),
            ),
        }
    )

    issues = validate_requirement(draft, timezone_name="Asia/Shanghai")

    assert {(issue.code, issue.field) for issue in issues} >= {
        (RequirementIssueCode.CONFLICT, "arrival.at"),
        (RequirementIssueCode.CONFLICT, "departure.at"),
    }


def test_validation_converts_aware_transport_times_to_request_timezone():
    draft = _complete_draft().model_copy(
        update={
            "arrival": AnchorDraft(
                name="杭州东站",
                at=datetime(2026, 10, 1, 17, 0, tzinfo=timezone.utc),
            )
        }
    )

    issues = validate_requirement(draft, timezone_name="Asia/Shanghai")

    assert not any(issue.field == "arrival.at" for issue in issues)


def test_assembler_uses_resolved_coordinates_and_deterministic_defaults():
    draft = _complete_draft().model_copy(
        update={"travelers": None, "daily_start": None, "daily_end": None}
    )
    resolutions = {
        "arrival": AnchorResolution(
            role="arrival",
            query_name="杭州东站",
            resolved_name="杭州东站",
            poi_id="hz_east_station",
            coordinate=Coordinate(longitude=120.212, latitude=30.2909),
            provider="mock",
            data_confidence=1.0,
        ),
        "departure": AnchorResolution(
            role="departure",
            query_name="杭州东站",
            resolved_name="杭州东站",
            poi_id="hz_east_station",
            coordinate=Coordinate(longitude=120.212, latitude=30.2909),
            provider="mock",
            data_confidence=1.0,
        ),
        "accommodation": AnchorResolution(
            role="accommodation",
            query_name="西湖东侧",
            resolved_name="西湖东侧",
            poi_id="hz_west_lake_east",
            coordinate=Coordinate(longitude=120.165, latitude=30.25),
            provider="mock",
            data_confidence=1.0,
        ),
    }

    trip = assemble_trip_spec(draft, resolutions, timezone_name="Asia/Shanghai")

    assert trip.travelers == 1
    assert trip.daily_start == time(9, 0)
    assert trip.daily_end == time(20, 0)
    assert trip.arrival.coordinate == resolutions["arrival"].coordinate
    assert trip.accommodation is not None
    assert trip.accommodation.coordinate == resolutions["accommodation"].coordinate
