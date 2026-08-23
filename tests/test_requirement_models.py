from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from travel_agent.requirements.models import (
    AnchorDraft,
    NaturalPlanningRequest,
    RequirementDraft,
)


CHINA_TZ = timezone(timedelta(hours=8))


def test_natural_request_normalizes_text_and_has_bounded_input():
    request = NaturalPlanningRequest(
        text="  10 月 2 日到杭州  ",
        reference_date=date(2026, 8, 23),
    )

    assert request.text == "10 月 2 日到杭州"
    assert request.timezone == "Asia/Shanghai"
    assert request.max_replan_rounds == 2

    with pytest.raises(ValidationError):
        NaturalPlanningRequest(
            text="x" * 4_001,
            reference_date=date(2026, 8, 23),
        )


def test_requirement_draft_allows_missing_fields_without_inventing_defaults():
    draft = RequirementDraft(destination="杭州")

    assert draft.destination == "杭州"
    assert draft.start_date is None
    assert draft.travelers is None
    assert draft.arrival is None
    assert draft.interests == []


def test_requirement_draft_keeps_extracted_anchor_and_soft_preferences():
    draft = RequirementDraft(
        destination="杭州",
        start_date=date(2026, 10, 2),
        end_date=date(2026, 10, 4),
        travelers=2,
        arrival=AnchorDraft(
            name="杭州东站",
            at=datetime(2026, 10, 2, 10, 30, tzinfo=CHINA_TZ),
        ),
        departure=AnchorDraft(
            name="杭州东站",
            at=datetime(2026, 10, 4, 19, 0, tzinfo=CHINA_TZ),
        ),
        total_budget=Decimal("3000"),
        interests=["自然", "美食"],
    )

    assert draft.arrival is not None
    assert draft.arrival.name == "杭州东站"
    assert draft.total_budget == Decimal("3000")
    assert draft.interests == ["自然", "美食"]

