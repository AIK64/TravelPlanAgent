from travel_agent.evaluation.memory import load_memory_scenarios, run_memory_ablations


def test_v11_memory_dataset_and_required_ablations():
    scenarios = load_memory_scenarios("evals/v1_1/memory_scenarios.jsonl")
    report = run_memory_ablations(scenarios)

    assert len(scenarios) == 60
    variants = {item.variant: item for item in report.variants}
    assert {
        "without_memory",
        "with_memory",
        "full_history",
        "confirmed_only",
        "inferred_memory",
    } <= variants.keys()
    assert variants["with_memory"].preference_hit_rate > variants["without_memory"].preference_hit_rate
    assert variants["confirmed_only"].wrong_personalization_rate <= variants["inferred_memory"].wrong_personalization_rate
    assert variants["with_memory"].explicit_override_accuracy == 1.0
