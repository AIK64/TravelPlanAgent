from __future__ import annotations

from collections.abc import Iterable

from travel_agent.requirements.models import (
    AnchorDraft,
    RequirementDraft,
    RequirementIssue,
    RequirementIssueCode,
    RequirementPatch,
)


_COMPOSITE_TARGET_FIELDS: dict[str, tuple[str, ...]] = {
    "date_range": ("start_date", "end_date"),
    "transport_time": ("arrival.at", "departure.at"),
    "daily_window": ("daily_start", "daily_end"),
}
_CONFLICT_TARGET_FIELDS: dict[str, tuple[str, ...]] = {
    "arrival.at": ("start_date", "arrival.at"),
    "departure.at": ("end_date", "departure.at"),
}
_ANCHOR_FIELD_TO_ROLE = {
    "arrival.name": "arrival",
    "departure.name": "departure",
    "accommodation_name": "accommodation",
    "destination": "arrival",
}


def clarification_target_fields(issues: Iterable[RequirementIssue]) -> list[str]:
    """将问题字段展开成当前一轮允许修改的最小字段集合。"""
    targets: list[str] = []
    for issue in issues:
        if not issue.blocking:
            continue
        fields = _COMPOSITE_TARGET_FIELDS.get(issue.field)
        if fields is None and issue.code is RequirementIssueCode.CONFLICT:
            fields = _CONFLICT_TARGET_FIELDS.get(issue.field)
        for field in fields or (issue.field,):
            if field not in targets:
                targets.append(field)
    return targets


def merge_requirement_patch(
    draft: RequirementDraft,
    patch: RequirementPatch,
    *,
    allowed_fields: Iterable[str],
) -> tuple[RequirementDraft, list[str], list[str]]:
    """只应用白名单内的非空字段，并返回 changed/rejected 字段。"""
    allowed = set(allowed_fields)
    values = _non_null_patch_values(patch)
    changed: list[str] = []
    rejected = sorted(field for field in values if field not in allowed)
    payload = draft.model_dump()

    for field in sorted(allowed):
        if field not in values:
            continue
        value = values[field]
        if _value_at(draft, field) == value:
            continue
        if "." not in field:
            payload[field] = value
        else:
            parent, child = field.split(".", maxsplit=1)
            current = payload.get(parent)
            nested = dict(current) if isinstance(current, dict) else {}
            nested[child] = value
            payload[parent] = nested
        changed.append(field)

    return RequirementDraft.model_validate(payload), changed, rejected


def invalidated_anchor_roles(changed_fields: Iterable[str]) -> list[str]:
    """计算字段变化后必须失效的地点解析结果。"""
    roles: set[str] = set()
    for field in changed_fields:
        if field == "destination":
            return ["accommodation", "arrival", "departure"]
        role = _ANCHOR_FIELD_TO_ROLE.get(field)
        if role:
            roles.add(role)
    return sorted(roles)


def _non_null_patch_values(patch: RequirementPatch) -> dict[str, object]:
    values: dict[str, object] = {}
    for field in (
        "destination",
        "start_date",
        "end_date",
        "travelers",
        "accommodation_name",
        "total_budget",
        "interests",
        "avoid",
        "must_visit",
        "pace",
        "mobility",
        "daily_start",
        "daily_end",
    ):
        value = getattr(patch, field)
        if value is not None:
            values[field] = value
    _add_anchor_values(values, "arrival", patch.arrival)
    _add_anchor_values(values, "departure", patch.departure)
    return values


def _add_anchor_values(
    values: dict[str, object],
    role: str,
    anchor: AnchorDraft | None,
) -> None:
    if anchor is None:
        return
    if anchor.name is not None:
        values[f"{role}.name"] = anchor.name
    if anchor.at is not None:
        values[f"{role}.at"] = anchor.at


def _value_at(draft: RequirementDraft, field: str) -> object:
    if "." not in field:
        return getattr(draft, field)
    parent, child = field.split(".", maxsplit=1)
    nested = getattr(draft, parent)
    return getattr(nested, child) if nested is not None else None
