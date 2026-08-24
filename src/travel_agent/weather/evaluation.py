from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from travel_agent.domain.weather_models import DailyWeather, WeatherRiskLevel
from travel_agent.weather.policy import (
    WEATHER_POLICY_VERSION,
    classify_daily_weather,
    normalize_phenomenon,
)


class WeatherEvalCase(BaseModel):
    """固定 Mock Fixture；Graph 结果字段来自离线轨迹标注。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    category: str
    risk_evaluable: bool = True
    day_weather: str = "晴"
    night_weather: str = "晴"
    high_celsius: int | None = 28
    low_celsius: int | None = 18
    day_wind_level: int | None = 3
    night_wind_level: int | None = 3
    expected_risk_level: WeatherRiskLevel = WeatherRiskLevel.NORMAL
    expected_event: bool = False
    detected_event: bool = False
    dedup_case: bool = False
    duplicate_suppressed: bool = True
    impact_evaluable: bool = True
    impact_match: bool = True
    locked_artifacts_preserved: bool = True
    unaffected_days_preserved: bool = True
    hard_constraint_regression: bool = False
    no_replan_expected: bool = False
    preview_expected: bool = False
    preview_created: bool = False
    commit_expected: bool = False
    version_committed: bool = False
    failure_case: bool = False
    failure_classification_match: bool = True
    bounded_termination: bool = True
    required_route_count: int = Field(default=0, ge=0)
    reused_route_count: int = Field(default=0, ge=0)


class WeatherEvalReport(BaseModel):
    dataset_version: str
    policy_version: str
    case_count: int
    weather_risk_accuracy: float
    event_detection_precision: float
    event_detection_recall: float
    event_detection_f1: float
    event_deduplication_rate: float | None
    impact_exact_match_rate: float
    locked_artifact_preservation_rate: float
    unaffected_day_preservation_rate: float
    hard_constraint_regression_rate: float
    false_replan_rate: float | None
    route_reuse_rate: float | None
    preview_correctness_rate: float
    commit_correctness_rate: float
    failure_classification_accuracy: float | None
    bounded_termination_rate: float
    duplicate_preview_count: int


def _rate(values: list[bool]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def _optional_rate(values: list[bool]) -> float | None:
    return _rate(values) if values else None


def _safe_ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _risk_matches(case: WeatherEvalCase) -> bool:
    risk = classify_daily_weather(
        DailyWeather(
            date="2026-10-02",
            day_phenomenon=normalize_phenomenon(case.day_weather),
            night_phenomenon=normalize_phenomenon(case.night_weather),
            high_celsius=case.high_celsius,
            low_celsius=case.low_celsius,
            day_wind_level=case.day_wind_level,
            night_wind_level=case.night_wind_level,
        )
    )
    return risk.level is case.expected_risk_level


def evaluate_weather_cases(
    cases: list[WeatherEvalCase], *, dataset_version: str = "weather-fixture-v1"
) -> WeatherEvalReport:
    risk_cases = [case for case in cases if case.risk_evaluable]
    true_positive = sum(case.expected_event and case.detected_event for case in cases)
    false_positive = sum(not case.expected_event and case.detected_event for case in cases)
    false_negative = sum(case.expected_event and not case.detected_event for case in cases)
    precision = _safe_ratio(true_positive, true_positive + false_positive)
    recall = _safe_ratio(true_positive, true_positive + false_negative)
    f1 = (
        round(2 * precision * recall / (precision + recall), 4)
        if precision + recall
        else 0.0
    )
    dedup_cases = [case for case in cases if case.dedup_case]
    impact_cases = [case for case in cases if case.impact_evaluable]
    no_replan_cases = [case for case in cases if case.no_replan_expected]
    failure_cases = [case for case in cases if case.failure_case]
    required_routes = sum(case.required_route_count for case in cases)
    reused_routes = sum(case.reused_route_count for case in cases)
    return WeatherEvalReport(
        dataset_version=dataset_version,
        policy_version=WEATHER_POLICY_VERSION,
        case_count=len(cases),
        weather_risk_accuracy=_rate([_risk_matches(case) for case in risk_cases]),
        event_detection_precision=precision,
        event_detection_recall=recall,
        event_detection_f1=f1,
        event_deduplication_rate=_optional_rate(
            [case.duplicate_suppressed for case in dedup_cases]
        ),
        impact_exact_match_rate=_rate(
            [case.impact_match for case in impact_cases]
        ),
        locked_artifact_preservation_rate=_rate(
            [case.locked_artifacts_preserved for case in cases]
        ),
        unaffected_day_preservation_rate=_rate(
            [case.unaffected_days_preserved for case in cases]
        ),
        hard_constraint_regression_rate=_rate(
            [case.hard_constraint_regression for case in cases]
        ),
        false_replan_rate=_optional_rate(
            [case.preview_created for case in no_replan_cases]
        ),
        route_reuse_rate=(
            round(reused_routes / required_routes, 4)
            if required_routes
            else None
        ),
        preview_correctness_rate=_rate(
            [case.preview_created == case.preview_expected for case in cases]
        ),
        commit_correctness_rate=_rate(
            [case.version_committed == case.commit_expected for case in cases]
        ),
        failure_classification_accuracy=_optional_rate(
            [case.failure_classification_match for case in failure_cases]
        ),
        bounded_termination_rate=_rate(
            [case.bounded_termination for case in cases]
        ),
        duplicate_preview_count=sum(
            not case.duplicate_suppressed for case in dedup_cases
        ),
    )
