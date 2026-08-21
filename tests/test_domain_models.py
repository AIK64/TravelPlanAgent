from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from travel_agent.domain.models import DayPlan, PlanMetrics, TripSpec


def test_trip_day_count(hangzhou_trip):
    assert hangzhou_trip.day_count == 3


def test_transport_anchor_requires_timezone(hangzhou_trip):
    payload = hangzhou_trip.model_dump()
    payload["arrival"]["at"] = datetime(2026, 10, 2, 10, 30)
    with pytest.raises(ValidationError, match="timezone-aware"):
        type(hangzhou_trip).model_validate(payload)


def test_trip_spec_strips_and_drops_blank_preference_terms(hangzhou_trip):
    """防止空白偏好进入子串匹配后把任意 POI 误判为必去地点。"""
    payload = hangzhou_trip.model_dump()
    payload.update(
        {
            "interests": [" 自然 ", "", "  ", "人文"],
            "avoid": [" 高强度 ", "\t"],
            "must_visit": [" 灵隐寺 ", "\n"],
        }
    )

    trip = TripSpec.model_validate(payload)

    assert trip.interests == ["自然", "人文"]
    assert trip.avoid == ["高强度"]
    assert trip.must_visit == ["灵隐寺"]


@pytest.mark.parametrize("field", ["interests", "avoid", "must_visit"])
def test_trip_spec_bounds_preference_list_size(hangzhou_trip, field):
    """防止单个请求用超大偏好列表无界占用内存并放大后续 Tool Use。"""
    payload = hangzhou_trip.model_dump()
    payload[field] = [f"term-{index}" for index in range(101)]

    with pytest.raises(ValidationError):
        TripSpec.model_validate(payload)


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


@pytest.mark.parametrize("cost_field", ["known_estimated_cost", "estimated_cost"])
def test_plan_metrics_rejects_negative_known_cost_for_both_input_names(cost_field):
    """防止新旧成本输入名之一绕过预算下界校验。"""
    with pytest.raises(ValidationError):
        PlanMetrics(
            preference_match=0.8,
            diversity=0.6,
            data_confidence=0.7,
            total_travel_minutes=40,
            walking_distance_meters=1200,
            fatigue_score=0.3,
            **{cost_field: Decimal("-1")},
        )

