from pathlib import Path

from travel_agent.lifecycle.evaluation import LifecycleEvalCase, evaluate_lifecycle_cases


ROOT = Path(__file__).resolve().parents[1]


def test_lifecycle_fixture_meets_v08_offline_gates():
    cases = [
        LifecycleEvalCase.model_validate_json(line)
        for line in (ROOT / "evals" / "lifecycle" / "cases.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    report = evaluate_lifecycle_cases(cases)

    assert report.case_count >= 15
    assert report.intent_exact_match_rate == 1
    assert report.grounding_accuracy == 1
    assert report.impact_exact_match_rate == 1
    assert report.locked_artifact_preservation_rate == 1
    assert report.unaffected_day_preservation_rate == 1
    assert report.hard_constraint_regression_rate == 0
    assert report.diff_exact_match_rate == 1
    assert report.commit_correctness_rate == 1
    assert report.idempotent_replay_rate == 1
    assert report.bounded_termination_rate == 1

