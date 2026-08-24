from datetime import datetime, timezone
from decimal import Decimal

from travel_agent.domain.models import (
    DayPlan,
    ItemType,
    PlanCandidate,
    PlanItem,
    PlanMetrics,
    PlanStyle,
)
from travel_agent.lifecycle.diff import build_plan_diff
from travel_agent.lifecycle.fingerprints import (
    day_fingerprint,
    item_fingerprint,
    plan_fingerprint,
    with_stable_item_ids,
)


def _candidate() -> PlanCandidate:
    item = PlanItem(
        type=ItemType.ACTIVITY,
        name="博物馆",
        poi_id="museum",
        start_at=datetime(2026, 10, 2, 9, tzinfo=timezone.utc),
        end_at=datetime(2026, 10, 2, 11, tzinfo=timezone.utc),
    )
    day = DayPlan(date=item.start_at.date(), theme="人文", primary_area="中心", items=[item])
    return PlanCandidate(
        id="balanced",
        style=PlanStyle.BALANCED,
        days=[day],
        metrics=PlanMetrics(
            preference_match=1,
            diversity=1,
            data_confidence=1,
            total_travel_minutes=0,
            walking_distance_meters=0,
            known_estimated_cost=Decimal("0"),
            fatigue_score=0,
        ),
    )


def test_versioned_item_and_fingerprints_are_stable():
    first = with_stable_item_ids("session", _candidate())
    second = with_stable_item_ids("session", _candidate())

    assert first.days[0].items[0].item_id == second.days[0].items[0].item_id
    assert item_fingerprint(first.days[0].items[0], first.days[0].date) == item_fingerprint(
        second.days[0].items[0], second.days[0].date
    )
    assert day_fingerprint(first.days[0]) == day_fingerprint(second.days[0])
    assert plan_fingerprint(first) == plan_fingerprint(second)


def test_plan_diff_detects_move_and_time_change():
    before = with_stable_item_ids("session", _candidate())
    item = before.days[0].items[0]
    moved = item.model_copy(
        update={
            "start_at": datetime(2026, 10, 3, 10, tzinfo=timezone.utc),
            "end_at": datetime(2026, 10, 3, 12, tzinfo=timezone.utc),
        }
    )
    after = before.model_copy(
        update={
            "days": [
                before.days[0].model_copy(update={"items": []}),
                DayPlan(
                    date=moved.start_at.date(),
                    theme="人文",
                    primary_area="中心",
                    items=[moved],
                ),
            ]
        }
    )

    diff = build_plan_diff(before, after, from_version_id="V1", to_id="P1")

    assert len(diff.moved_items) == 1
    assert diff.moved_items[0].item_id == item.item_id
    assert len(diff.time_changes) == 1

