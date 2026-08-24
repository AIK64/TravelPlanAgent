from __future__ import annotations

from travel_agent.domain.models import PlanningPOI
from travel_agent.domain.weather_models import ActivityExposure, ExposureKind


INDOOR_TERMS = {
    "博物馆",
    "美术馆",
    "展览馆",
    "展馆",
    "商场",
    "购物中心",
    "室内",
    "剧院",
    "科技馆",
}
OUTDOOR_TERMS = {
    "自然",
    "公园",
    "湿地",
    "山岳",
    "植物园",
    "动物园",
    "广场",
}
MIXED_TERMS = {"寺庙", "街区", "古镇", "景区", "校园"}


def classify_planning_poi(poi: PlanningPOI, *, item_id: str) -> ActivityExposure:
    categories = {item.strip() for item in poi.facts.categories}
    name = poi.facts.name.strip()
    if categories & INDOOR_TERMS or any(term in name for term in INDOOR_TERMS):
        return ActivityExposure(
            item_id=item_id,
            exposure=ExposureKind.INDOOR,
            rule_id="category.indoor.v1",
            confidence=0.95 if categories & INDOOR_TERMS else 0.8,
        )
    if categories & OUTDOOR_TERMS or any(term in name for term in OUTDOOR_TERMS):
        return ActivityExposure(
            item_id=item_id,
            exposure=ExposureKind.OUTDOOR,
            rule_id="category.outdoor.v1",
            confidence=0.95 if categories & OUTDOOR_TERMS else 0.8,
        )
    if categories & MIXED_TERMS or any(term in name for term in MIXED_TERMS):
        return ActivityExposure(
            item_id=item_id,
            exposure=ExposureKind.MIXED,
            rule_id="category.mixed.v1",
            confidence=0.85,
        )
    return ActivityExposure(
        item_id=item_id,
        exposure=ExposureKind.UNKNOWN,
        rule_id=None,
        confidence=0.0,
    )


def indoor_alternatives(
    planning_pois: tuple[PlanningPOI, ...],
    *,
    excluded_poi_ids: set[str],
    minimum_confidence: float = 0.8,
) -> tuple[PlanningPOI, ...]:
    eligible = []
    for poi in planning_pois:
        if poi.facts.id in excluded_poi_ids:
            continue
        exposure = classify_planning_poi(poi, item_id=poi.facts.id)
        if (
            exposure.exposure is ExposureKind.INDOOR
            and exposure.confidence >= minimum_confidence
        ):
            eligible.append(poi)
    return tuple(
        sorted(
            eligible,
            key=lambda item: (-item.data_confidence, item.facts.id),
        )
    )
