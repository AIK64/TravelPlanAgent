from __future__ import annotations

import re
from collections.abc import Collection
from typing import Literal

from pydantic import BaseModel

from travel_agent.domain.tool_models import (
    POIFacts,
    POISearchQuery,
    ToolResult,
    ToolStatus,
)
from travel_agent.requirements.models import (
    AnchorResolution,
    RequirementDraft,
    RequirementIssue,
    RequirementIssueCode,
)


AnchorRole = Literal["arrival", "departure", "accommodation"]


class AnchorSearchIntent(BaseModel):
    roles: list[AnchorRole]
    query: POISearchQuery


def build_anchor_search_plan(
    draft: RequirementDraft,
    *,
    roles: Collection[AnchorRole] | None = None,
) -> list[AnchorSearchIntent]:
    """按到达、离开、住宿顺序生成去重的精确地点检索意图。"""
    if not draft.destination:
        raise ValueError("destination is required before anchor resolution")
    candidates: list[tuple[AnchorRole, str | None]] = [
        ("arrival", draft.arrival.name if draft.arrival else None),
        ("departure", draft.departure.name if draft.departure else None),
        ("accommodation", draft.accommodation_name),
    ]
    intents_by_name: dict[str, AnchorSearchIntent] = {}
    for role, name in candidates:
        if roles is not None and role not in roles:
            continue
        if not name:
            continue
        key = _normalize(name)
        existing = intents_by_name.get(key)
        if existing is not None:
            existing.roles.append(role)
            continue
        intents_by_name[key] = AnchorSearchIntent(
            roles=[role],
            query=POISearchQuery(
                city=draft.destination,
                keyword=name,
                exact_match=True,
                limit=5,
                priority=200,
            ),
        )
    return list(intents_by_name.values())


def resolve_anchor_search_results(
    plan: list[AnchorSearchIntent],
    results: list[ToolResult[list[POIFacts]]],
) -> tuple[dict[str, AnchorResolution], list[RequirementIssue]]:
    """只接受唯一、可解释的名称匹配；空结果和歧义转为澄清问题。"""
    if len(plan) != len(results):
        raise ValueError("anchor plan and results must have the same length")
    resolutions: dict[str, AnchorResolution] = {}
    issues: list[RequirementIssue] = []
    for intent, result in zip(plan, results, strict=True):
        if result.status is ToolStatus.FAILED or result.data is None:
            raise ValueError("failed tool results must be handled before resolution")
        matches = _matching_facts(intent.query.keyword, result.data)
        if len(matches) != 1:
            code = (
                RequirementIssueCode.NOT_FOUND
                if not matches
                else RequirementIssueCode.AMBIGUOUS
            )
            for role in intent.roles:
                issues.append(_anchor_issue(role, intent.query.keyword, code))
            continue

        facts = matches[0]
        for role in intent.roles:
            resolutions[role] = AnchorResolution(
                role=role,
                query_name=intent.query.keyword,
                resolved_name=facts.name,
                poi_id=facts.id,
                coordinate=facts.coordinate,
                provider=facts.provider,
                data_confidence=facts.data_confidence,
            )
    return resolutions, issues


def _matching_facts(query_name: str, facts: list[POIFacts]) -> list[POIFacts]:
    query = _normalize(query_name)
    exact = [item for item in facts if _normalize(item.name) == query]
    if exact:
        return exact
    return [
        item
        for item in facts
        if query in _normalize(item.name) or _normalize(item.name) in query
    ]


def _normalize(value: str) -> str:
    return re.sub(r"[\s()（）]", "", value.strip().casefold())


def _anchor_issue(
    role: AnchorRole,
    name: str,
    code: RequirementIssueCode,
) -> RequirementIssue:
    role_names = {
        "arrival": "抵达地点",
        "departure": "离开地点",
        "accommodation": "住宿地点",
    }
    field = f"{role}.name" if role != "accommodation" else "accommodation_name"
    reason = "没有找到唯一地点" if code is RequirementIssueCode.NOT_FOUND else "匹配到多个地点"
    return RequirementIssue(
        code=code,
        field=field,
        message=f"{role_names[role]}“{name}”{reason}",
        question=f"请提供更具体的{role_names[role]}名称或地址。",
        blocking=True,
    )
