from __future__ import annotations

from datetime import date, datetime, time, timezone, timedelta
from decimal import Decimal

import pytest

from travel_agent.domain.models import (
    Coordinate,
    LocationAnchor,
    MobilityConstraints,
    Pace,
    TransportAnchor,
    TripSpec,
)


CHINA_TZ = timezone(timedelta(hours=8))


@pytest.fixture
def hangzhou_trip() -> TripSpec:
    return TripSpec(
        destination="杭州",
        start_date=date(2026, 10, 2),
        end_date=date(2026, 10, 4),
        travelers=3,
        arrival=TransportAnchor(
            name="杭州东站",
            at=datetime(2026, 10, 2, 10, 30, tzinfo=CHINA_TZ),
            coordinate=Coordinate(longitude=120.2120, latitude=30.2909),
        ),
        departure=TransportAnchor(
            name="杭州东站",
            at=datetime(2026, 10, 4, 19, 0, tzinfo=CHINA_TZ),
            coordinate=Coordinate(longitude=120.2120, latitude=30.2909),
        ),
        accommodation=LocationAnchor(
            name="西湖东侧",
            coordinate=Coordinate(longitude=120.1650, latitude=30.2500),
        ),
        total_budget=Decimal("1500"),
        interests=["自然", "美食", "人文"],
        avoid=["高强度"],
        must_visit=["灵隐寺"],
        pace=Pace.RELAXED,
        mobility=MobilityConstraints(
            max_daily_walking_meters=6_000,
            max_daily_activity_minutes=360,
            needs_frequent_rest=True,
        ),
        daily_start=time(9, 0),
        daily_end=time(20, 0),
    )

