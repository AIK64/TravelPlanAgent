from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from travel_agent.domain.models import (
    LocationAnchor,
    MobilityConstraints,
    Pace,
    TransportAnchor,
    TripSpec,
)
from travel_agent.requirements.models import (
    AnchorResolution,
    RequirementDraft,
    RequirementIssue,
    RequirementIssueCode,
)


_MISSING_QUESTIONS = {
    "destination": "这次旅行的目的地城市是哪里？",
    "start_date": "行程从哪一天开始？请提供完整日期。",
    "end_date": "行程在哪一天结束？请提供完整日期。",
    "arrival.name": "你会从哪个车站、机场或地点抵达？",
    "arrival.at": "预计何时抵达？请提供日期和时间。",
    "departure.name": "你会从哪个车站、机场或地点离开？",
    "departure.at": "预计何时离开？请提供日期和时间。",
}


def _issue(
    code: RequirementIssueCode,
    field: str,
    message: str,
    question: str | None = None,
) -> RequirementIssue:
    return RequirementIssue(
        code=code,
        field=field,
        message=message,
        question=question,
        blocking=True,
    )


def validate_requirement(
    draft: RequirementDraft,
    *,
    timezone_name: str = "Asia/Shanghai",
) -> list[RequirementIssue]:
    """以确定性规则判断语义抽取结果能否安全构造 TripSpec。"""
    issues: list[RequirementIssue] = []
    missing = {
        "destination": not draft.destination,
        "start_date": draft.start_date is None,
        "end_date": draft.end_date is None,
        "arrival.name": draft.arrival is None or not draft.arrival.name,
        "arrival.at": draft.arrival is None or draft.arrival.at is None,
        "departure.name": draft.departure is None or not draft.departure.name,
        "departure.at": draft.departure is None or draft.departure.at is None,
    }
    for field, is_missing in missing.items():
        if is_missing:
            issues.append(
                _issue(
                    RequirementIssueCode.MISSING,
                    field,
                    f"缺少必要字段 {field}",
                    _MISSING_QUESTIONS[field],
                )
            )

    if (
        draft.start_date is not None
        and draft.end_date is not None
        and draft.end_date < draft.start_date
    ):
        issues.append(
            _issue(
                RequirementIssueCode.CONFLICT,
                "date_range",
                "行程结束日期早于开始日期",
                "请确认正确的开始日期和结束日期。",
            )
        )

    arrival_at = draft.arrival.at if draft.arrival else None
    departure_at = draft.departure.at if draft.departure else None
    if arrival_at is not None and departure_at is not None:
        comparable_arrival = _localized(arrival_at, timezone_name)
        comparable_departure = _localized(departure_at, timezone_name)
        if comparable_departure <= comparable_arrival:
            issues.append(
                _issue(
                    RequirementIssueCode.CONFLICT,
                    "transport_time",
                    "离开时间必须晚于抵达时间",
                    "请确认正确的抵达和离开日期时间。",
                )
            )

    if arrival_at is not None and draft.start_date is not None:
        if _localized(arrival_at, timezone_name).date() < draft.start_date:
            issues.append(
                _issue(
                    RequirementIssueCode.CONFLICT,
                    "arrival.at",
                    "抵达日期不能早于行程开始日期",
                    "请确认行程开始日期或抵达时间。",
                )
            )
    if departure_at is not None and draft.end_date is not None:
        if _localized(departure_at, timezone_name).date() > draft.end_date:
            issues.append(
                _issue(
                    RequirementIssueCode.CONFLICT,
                    "departure.at",
                    "离开日期不能晚于行程结束日期",
                    "请确认行程结束日期或离开时间。",
                )
            )

    if draft.travelers is not None and not 1 <= draft.travelers <= 20:
        issues.append(
            _issue(
                RequirementIssueCode.INVALID,
                "travelers",
                "出行人数必须在 1 到 20 之间",
                "这次共有多少位旅行者？",
            )
        )
    if draft.total_budget is not None and draft.total_budget <= 0:
        issues.append(
            _issue(
                RequirementIssueCode.INVALID,
                "total_budget",
                "预算必须为正数",
                "请提供一个大于 0 的总预算，或说明暂不限制预算。",
            )
        )
    if (
        draft.daily_start is not None
        and draft.daily_end is not None
        and draft.daily_end <= draft.daily_start
    ):
        issues.append(
            _issue(
                RequirementIssueCode.CONFLICT,
                "daily_window",
                "每日结束时间必须晚于开始时间",
                "每天希望从几点游玩到几点？",
            )
        )
    return issues


def _localized(value: datetime, timezone_name: str) -> datetime:
    target_timezone = ZoneInfo(timezone_name)
    if value.tzinfo is not None and value.utcoffset() is not None:
        return value.astimezone(target_timezone)
    return value.replace(tzinfo=target_timezone)


def assemble_trip_spec(
    draft: RequirementDraft,
    resolutions: dict[str, AnchorResolution],
    *,
    timezone_name: str,
) -> TripSpec:
    """仅使用用户抽取值、显式默认值和工具坐标组装严格领域模型。"""
    issues = validate_requirement(draft, timezone_name=timezone_name)
    if issues:
        fields = ", ".join(issue.field for issue in issues)
        raise ValueError(f"cannot assemble TripSpec with requirement issues: {fields}")

    arrival_resolution = resolutions.get("arrival")
    departure_resolution = resolutions.get("departure")
    if arrival_resolution is None or departure_resolution is None:
        raise ValueError("arrival and departure anchors must be resolved")

    assert draft.destination is not None
    assert draft.start_date is not None
    assert draft.end_date is not None
    assert draft.arrival is not None and draft.arrival.name and draft.arrival.at
    assert draft.departure is not None and draft.departure.name and draft.departure.at

    accommodation = None
    if draft.accommodation_name:
        accommodation_resolution = resolutions.get("accommodation")
        if accommodation_resolution is None:
            raise ValueError("accommodation anchor must be resolved")
        accommodation = LocationAnchor(
            name=accommodation_resolution.resolved_name,
            coordinate=accommodation_resolution.coordinate,
        )

    return TripSpec(
        destination=draft.destination,
        start_date=draft.start_date,
        end_date=draft.end_date,
        travelers=draft.travelers if draft.travelers is not None else 1,
        arrival=TransportAnchor(
            name=arrival_resolution.resolved_name,
            at=_localized(draft.arrival.at, timezone_name),
            coordinate=arrival_resolution.coordinate,
        ),
        departure=TransportAnchor(
            name=departure_resolution.resolved_name,
            at=_localized(draft.departure.at, timezone_name),
            coordinate=departure_resolution.coordinate,
        ),
        accommodation=accommodation,
        total_budget=draft.total_budget,
        interests=draft.interests,
        avoid=draft.avoid,
        must_visit=draft.must_visit,
        pace=draft.pace or Pace.BALANCED,
        mobility=draft.mobility or MobilityConstraints(),
        daily_start=draft.daily_start or time(9, 0),
        daily_end=draft.daily_end or time(20, 0),
    )
