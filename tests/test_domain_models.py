from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from travel_agent.domain.models import DayPlan, PlanMetrics


def test_trip_day_count(hangzhou_trip):
    assert hangzhou_trip.day_count == 3


def test_transport_anchor_requires_timezone(hangzhou_trip):
    payload = hangzhou_trip.model_dump()
    payload["arrival"]["at"] = datetime(2026, 10, 2, 10, 30)
    with pytest.raises(ValidationError, match="timezone-aware"):
        type(hangzhou_trip).model_validate(payload)


def test_cost_compatibility_fields_serialize_known_cost():
    """防止成本字段迁移后破坏旧响应，同时误把未知成本写成零成本。"""
    day = DayPlan(
        date=date(2026, 10, 2),
        theme="人文",
        primary_area="杭州",
        items=[],
        estimated_cost=Decimal("120"),
        unknown_cost_item_count=2,
    )
    metrics = PlanMetrics(
        preference_match=0.8,
        diversity=0.6,
        data_confidence=0.7,
        total_travel_minutes=40,
        walking_distance_meters=1200,
        estimated_cost=Decimal("120"),
        unknown_cost_item_count=2,
        fatigue_score=0.3,
    )

    assert day.model_dump()["known_estimated_cost"] == Decimal("120")
    assert day.model_dump()["estimated_cost"] == Decimal("120")
    assert day.model_dump()["unknown_cost_item_count"] == 2
    assert metrics.model_dump()["estimated_cost"] == Decimal("120")
    assert metrics.model_dump()["unknown_cost_item_count"] == 2

