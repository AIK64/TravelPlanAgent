from pathlib import Path

from travel_agent.critique.evaluation import SoftCriticEvalCase, evaluate_cases


def test_soft_critic_fixture_set_has_fifteen_cases_and_reports_ablation():
    path = Path("evals/soft_critic/cases.jsonl")
    cases = [
        SoftCriticEvalCase.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    report = evaluate_cases(cases)
    assert report.case_count >= 15
    assert report.referential_grounding_rate == 1
    assert report.suggested_action_safety_rate == 1
    assert report.hard_constraint_regression_rate == 0
    assert (
        report.selection_agreement_with_critic
        >= report.selection_agreement_without_critic
    )
