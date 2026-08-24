from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
import math
from typing import Iterable

from travel_agent.domain.models import MobilityConstraints, Pace, TripSpec
from travel_agent.memory.models import (
    AgentRole,
    ContextManifest,
    MemoryCategory,
    MemoryConflict,
    PreferenceContext,
    PreferenceMemory,
    PreferenceSummary,
    utcnow,
)
from travel_agent.requirements.models import RequirementDraft


class PreferenceContextComposer:
    """确定性地检索、裁剪并应用已确认的偏好。"""

    policy_version = "memory-context-v1"

    def __init__(self, *, max_tokens: int = 1_200, max_characters: int = 4_800):
        if max_tokens < 32 or max_characters < 128:
            raise ValueError("context budgets are too small")
        self.max_tokens = max_tokens
        self.max_characters = max_characters

    def compose(
        self,
        memories: Iterable[PreferenceMemory],
        *,
        trip: TripSpec,
        draft: RequirementDraft | None,
        agent_role: AgentRole,
        now: datetime | None = None,
    ) -> PreferenceContext:
        current = now or utcnow()
        active: list[tuple[float, PreferenceMemory, str]] = []
        exclusion: dict[str, int] = {}
        overridden: list[str] = []
        for memory in memories:
            if not memory.active_at(current):
                exclusion["inactive"] = exclusion.get("inactive", 0) + 1
                continue
            if not self._scope_matches(memory, trip):
                exclusion["scope_mismatch"] = exclusion.get("scope_mismatch", 0) + 1
                continue
            if self._explicitly_overridden(memory, draft):
                overridden.append(memory.memory_id)
                exclusion["current_request_override"] = (
                    exclusion.get("current_request_override", 0) + 1
                )
                continue
            role_weight = self._role_weight(memory.category, agent_role)
            if role_weight <= 0:
                exclusion["role_irrelevant"] = exclusion.get("role_irrelevant", 0) + 1
                continue
            freshness = self._freshness(memory, current)
            score = round(
                4.0
                + role_weight
                + memory.confidence * 2.0
                + freshness,
                6,
            )
            active.append(
                (
                    score,
                    memory,
                    f"confirmed {memory.category.value} relevant to {agent_role.value}",
                )
            )
        active.sort(key=lambda item: (-item[0], item[1].memory_id))

        conflict_categories: set[MemoryCategory] = set()
        conflicts: list[MemoryConflict] = []
        first_by_category: dict[MemoryCategory, PreferenceMemory] = {}
        for _, memory, _ in active:
            previous = first_by_category.get(memory.category)
            if previous is None:
                first_by_category[memory.category] = memory
            elif previous.value != memory.value:
                conflict_categories.add(memory.category)
                conflicts.append(
                    MemoryConflict(
                        category=memory.category,
                        current_memory_id=previous.memory_id,
                        conflicting_memory_id=memory.memory_id,
                        resolution="user_confirmation_required",
                    )
                )

        summaries: list[PreferenceSummary] = []
        characters = 0
        for score, memory, reason in active:
            if memory.category in conflict_categories:
                exclusion["memory_conflict"] = (
                    exclusion.get("memory_conflict", 0) + 1
                )
                continue
            summary = PreferenceSummary(
                memory_id=memory.memory_id,
                category=memory.category,
                value=memory.value,
                confidence=memory.confidence,
                source=memory.source,
                reason=reason,
                score=score,
            )
            size = len(summary.model_dump_json())
            estimated_tokens = math.ceil((characters + size) / 4)
            if (
                characters + size > self.max_characters
                or estimated_tokens > self.max_tokens
            ):
                exclusion["context_budget"] = exclusion.get("context_budget", 0) + 1
                continue
            summaries.append(summary)
            characters += size

        manifest_payload = {
            "agent_role": agent_role.value,
            "selected": [item.memory_id for item in summaries],
            "overridden": sorted(overridden),
            "policy": self.policy_version,
        }
        content_hash = sha256(
            json.dumps(
                manifest_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return PreferenceContext(
            summaries=tuple(summaries),
            manifest=ContextManifest(
                context_id=f"ctx-{content_hash[:24]}",
                agent_role=agent_role,
                selected_memory_ids=tuple(item.memory_id for item in summaries),
                selected_categories=tuple(item.category for item in summaries),
                excluded_count=sum(exclusion.values()),
                exclusion_reasons=exclusion,
                estimated_tokens=math.ceil(characters / 4),
                character_count=characters,
                max_tokens=self.max_tokens,
                max_characters=self.max_characters,
                overridden_memory_ids=tuple(sorted(overridden)),
                content_hash=content_hash,
            ),
            conflicts=tuple(conflicts),
        )

    def apply_to_trip(
        self,
        trip: TripSpec,
        *,
        draft: RequirementDraft | None,
        context: PreferenceContext,
    ) -> TripSpec:
        updates: dict[str, object] = {}
        interests = list(trip.interests)
        avoid = list(trip.avoid)
        must_visit_keys = {item.casefold() for item in trip.must_visit}
        for summary in context.summaries:
            if summary.category is MemoryCategory.PACE and (
                draft is None or draft.pace is None
            ):
                updates["pace"] = Pace(str(summary.value))
            elif summary.category is MemoryCategory.WALKING_TOLERANCE and (
                draft is None or draft.mobility is None
            ):
                updates["mobility"] = trip.mobility.model_copy(
                    update={"max_daily_walking_meters": int(summary.value)}
                )
            elif summary.category is MemoryCategory.PREFERRED_CATEGORIES:
                interests = self._merge_terms(interests, summary.value)
            elif summary.category is MemoryCategory.AVOIDED_CATEGORIES:
                candidates = [
                    value
                    for value in self._as_strings(summary.value)
                    if value.casefold() not in must_visit_keys
                ]
                avoid = self._merge_terms(avoid, candidates)
            elif summary.category is MemoryCategory.ACCESSIBILITY_NEEDS:
                needs = {item.casefold() for item in self._as_strings(summary.value)}
                if "frequent_rest" in needs and (
                    draft is None or draft.mobility is None
                ):
                    base = updates.get("mobility", trip.mobility)
                    assert isinstance(base, MobilityConstraints)
                    updates["mobility"] = base.model_copy(
                        update={"needs_frequent_rest": True}
                    )
        updates["interests"] = interests
        updates["avoid"] = avoid
        return trip.model_copy(update=updates)

    @staticmethod
    def _as_strings(value: object) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value]
        return [str(value)]

    def _merge_terms(self, current: list[str], value: object) -> list[str]:
        result = list(current)
        seen = {item.casefold() for item in result}
        for item in self._as_strings(value):
            if item.casefold() not in seen:
                result.append(item)
                seen.add(item.casefold())
        return result

    @staticmethod
    def _scope_matches(memory: PreferenceMemory, trip: TripSpec) -> bool:
        if memory.scope.value == "global":
            return True
        if memory.scope.value == "destination":
            return bool(
                memory.scope_key
                and memory.scope_key.casefold() == trip.destination.casefold()
            )
        return True

    @staticmethod
    def _explicitly_overridden(
        memory: PreferenceMemory, draft: RequirementDraft | None
    ) -> bool:
        if draft is None:
            return False
        mapping = {
            MemoryCategory.PACE: draft.pace is not None,
            MemoryCategory.WALKING_TOLERANCE: draft.mobility is not None,
            MemoryCategory.PREFERRED_CATEGORIES: bool(draft.interests),
            MemoryCategory.AVOIDED_CATEGORIES: bool(draft.avoid),
        }
        return mapping.get(memory.category, False)

    @staticmethod
    def _role_weight(category: MemoryCategory, role: AgentRole) -> float:
        weights = {
            AgentRole.PLANNER: {
                MemoryCategory.PACE: 3.0,
                MemoryCategory.PREFERRED_CATEGORIES: 3.0,
                MemoryCategory.AVOIDED_CATEGORIES: 3.0,
                MemoryCategory.WALKING_TOLERANCE: 3.0,
                MemoryCategory.PREFERRED_TRANSPORT: 2.0,
                MemoryCategory.FOOD_PREFERENCES: 2.0,
                MemoryCategory.SCHEDULE_PREFERENCES: 2.0,
                MemoryCategory.ACCESSIBILITY_NEEDS: 3.0,
                MemoryCategory.BUDGET_STYLE: 2.0,
            },
            AgentRole.CRITIC: {
                MemoryCategory.PACE: 3.0,
                MemoryCategory.PREFERRED_CATEGORIES: 2.0,
                MemoryCategory.AVOIDED_CATEGORIES: 3.0,
                MemoryCategory.WALKING_TOLERANCE: 2.0,
                MemoryCategory.FOOD_PREFERENCES: 2.0,
                MemoryCategory.ACCESSIBILITY_NEEDS: 3.0,
                MemoryCategory.SCHEDULE_PREFERENCES: 3.0,
            },
            AgentRole.REPLANNER: {
                MemoryCategory.PACE: 2.0,
                MemoryCategory.AVOIDED_CATEGORIES: 3.0,
                MemoryCategory.WALKING_TOLERANCE: 3.0,
                MemoryCategory.PREFERRED_TRANSPORT: 2.0,
                MemoryCategory.ACCESSIBILITY_NEEDS: 3.0,
            },
        }
        return weights.get(role, {}).get(category, 0.0)

    @staticmethod
    def _freshness(memory: PreferenceMemory, now: datetime) -> float:
        age_days = max((now - memory.updated_at).total_seconds() / 86_400, 0)
        return max(1.0 - age_days / 365, 0.0)
