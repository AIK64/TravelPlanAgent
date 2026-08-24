from pathlib import Path

from travel_agent.weather.evaluation import WeatherEvalCase, evaluate_weather_cases


ROOT = Path(__file__).resolve().parents[1]


def test_weather_fixture_meets_v09_offline_gates():
    cases = [
        WeatherEvalCase.model_validate_json(line)
        for line in (ROOT / "evals" / "weather" / "cases.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    report = evaluate_weather_cases(cases)

    assert report.case_count >= 30
    assert report.policy_version == "weather-risk-v1"
    assert report.weather_risk_accuracy == 1
    assert report.event_detection_f1 >= 0.95
    assert report.event_deduplication_rate == 1
    assert report.impact_exact_match_rate >= 0.95
    assert report.locked_artifact_preservation_rate == 1
    assert report.unaffected_day_preservation_rate == 1
    assert report.hard_constraint_regression_rate == 0
    assert report.false_replan_rate == 0
    assert report.route_reuse_rate is not None
    assert report.route_reuse_rate >= 0.6
    assert report.preview_correctness_rate == 1
    assert report.commit_correctness_rate == 1
    assert report.failure_classification_accuracy == 1
    assert report.bounded_termination_rate == 1
    assert report.duplicate_preview_count == 0
