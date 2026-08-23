from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from travel_agent.requirements.clarification import (
    clarification_target_fields,
    invalidated_anchor_roles,
    merge_requirement_patch,
)
from travel_agent.requirements.models import (
    AnchorDraft,
    RequirementDraft,
    RequirementIssue,
    RequirementIssueCode,
    RequirementPatch,
)


def test_conflict_issues_expand_to_minimal_editable_fields():
    fields = clarification_target_fields(
        [
            RequirementIssue(
                code=RequirementIssueCode.CONFLICT,
                field="date_range",
                message="日期冲突",
                question="请确认日期",
            ),
            RequirementIssue(
                code=RequirementIssueCode.CONFLICT,
                field="arrival.at",
                message="抵达时间冲突",
                question="请确认抵达时间",
            ),
        ]
    )

    assert fields == ["start_date", "end_date", "arrival.at"]


def test_merge_patch_only_changes_allowed_nested_field():
    timezone = ZoneInfo("Asia/Shanghai")
    draft = RequirementDraft(
        destination="杭州",
        start_date=date(2026, 10, 2),
        end_date=date(2026, 10, 4),
        travelers=2,
        arrival=AnchorDraft(
            name="杭州东站",
            at=datetime(2026, 10, 2, 10, 30, tzinfo=timezone),
        ),
    )
    patch = RequirementPatch(
        destination="上海",
        departure=AnchorDraft(
            name="杭州东站",
            at=datetime(2026, 10, 4, 19, 0, tzinfo=timezone),
        ),
    )

    merged, changed, rejected = merge_requirement_patch(
        draft,
        patch,
        allowed_fields=["departure.name", "departure.at"],
    )

    assert merged.destination == "杭州"
    assert merged.departure is not None
    assert merged.departure.name == "杭州东站"
    assert merged.departure.at == datetime(2026, 10, 4, 19, 0, tzinfo=timezone)
    assert changed == ["departure.at", "departure.name"]
    assert rejected == ["destination"]


def test_anchor_invalidation_is_local_except_destination_change():
    assert invalidated_anchor_roles(["departure.name", "departure.at"]) == [
        "departure"
    ]
    assert invalidated_anchor_roles(["destination"]) == [
        "accommodation",
        "arrival",
        "departure",
    ]
