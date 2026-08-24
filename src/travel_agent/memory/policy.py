from __future__ import annotations

from hashlib import sha256
import json
import re
from typing import Any

from travel_agent.memory.errors import MemoryPolicyError
from travel_agent.memory.models import (
    MemoryCategory,
    MemorySource,
    PreferenceScope,
    PreferenceValue,
)


POLICY_VERSION = "preference-policy-v1"

_LIST_CATEGORIES = {
    MemoryCategory.PREFERRED_CATEGORIES,
    MemoryCategory.AVOIDED_CATEGORIES,
    MemoryCategory.PREFERRED_TRANSPORT,
    MemoryCategory.FOOD_PREFERENCES,
    MemoryCategory.ACCESSIBILITY_NEEDS,
}


def normalize_preference_value(
    category: MemoryCategory, value: PreferenceValue
) -> PreferenceValue:
    _reject_sensitive_value(value)
    if category in _LIST_CATEGORIES:
        values = value if isinstance(value, list) else [value]
        normalized: list[str] = []
        seen: set[str] = set()
        for item in values:
            if not isinstance(item, str):
                raise MemoryPolicyError(
                    "invalid_preference_value",
                    f"{category.value} requires string values",
                )
            text = item.strip()
            key = text.casefold()
            if text and key not in seen:
                normalized.append(text)
                seen.add(key)
        if not normalized:
            raise MemoryPolicyError(
                "invalid_preference_value",
                f"{category.value} must not be empty",
            )
        return normalized
    if category is MemoryCategory.PACE:
        if not isinstance(value, str) or value.strip().lower() not in {
            "relaxed",
            "balanced",
            "intensive",
        }:
            raise MemoryPolicyError(
                "invalid_preference_value",
                "pace must be relaxed, balanced, or intensive",
            )
        return value.strip().lower()
    if category is MemoryCategory.WALKING_TOLERANCE:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise MemoryPolicyError(
                "invalid_preference_value",
                "walking_tolerance requires a numeric meter value",
            )
        meters = int(value)
        if not 0 <= meters <= 50_000:
            raise MemoryPolicyError(
                "invalid_preference_value",
                "walking_tolerance must be between 0 and 50000 meters",
            )
        return meters
    if category is MemoryCategory.BUDGET_STYLE:
        if not isinstance(value, str) or value.strip().lower() not in {
            "economy",
            "balanced",
            "comfort",
        }:
            raise MemoryPolicyError(
                "invalid_preference_value",
                "budget_style must be economy, balanced, or comfort",
            )
        return value.strip().lower()
    if category is MemoryCategory.SCHEDULE_PREFERENCES:
        if not isinstance(value, dict):
            raise MemoryPolicyError(
                "invalid_preference_value",
                "schedule_preferences requires an object",
            )
        allowed = {"earliest_start", "latest_end", "avoid_early_start"}
        if set(value) - allowed:
            raise MemoryPolicyError(
                "invalid_preference_value",
                "schedule_preferences contains unsupported fields",
            )
        return value
    raise MemoryPolicyError(
        "unsupported_preference_category",
        f"unsupported preference category: {category.value}",
    )


def _reject_sensitive_value(value: PreferenceValue) -> None:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
    normalized = encoded.casefold()
    forbidden_markers = {
        "身份证",
        "护照",
        "银行卡",
        "信用卡",
        "支付密码",
        "passport",
        "identity card",
        "credit card",
        "payment password",
    }
    long_number = re.search(r"(?<!\d)\d{12,19}(?!\d)", normalized)
    if any(marker in normalized for marker in forbidden_markers) or long_number:
        raise MemoryPolicyError(
            "sensitive_memory_rejected",
            "sensitive identity or payment data must not be stored as preference memory",
        )


def validate_source_for_direct_write(source: MemorySource) -> None:
    if source not in {MemorySource.EXPLICIT_USER, MemorySource.IMPORT}:
        raise MemoryPolicyError(
            "confirmation_required",
            "only explicit user input or import may be written directly",
        )


def preference_content_hash(
    *,
    category: MemoryCategory,
    value: PreferenceValue,
    scope: PreferenceScope,
    scope_key: str | None,
) -> str:
    payload: dict[str, Any] = {
        "category": category.value,
        "value": value,
        "scope": scope.value,
        "scope_key": scope_key,
        "policy_version": POLICY_VERSION,
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(encoded).hexdigest()
