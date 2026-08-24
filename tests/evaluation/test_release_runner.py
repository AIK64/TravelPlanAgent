from pathlib import Path

import pytest

from travel_agent.evaluation.runner import run_release_evaluation
from travel_agent.evaluation.ablations import run_ablation_evaluation
from travel_agent.evaluation.models import EvaluationVariant


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_mock_release_gate_executes_120_cases_and_is_reproducible():
    manifest = ROOT / "evals" / "v1_0" / "manifest.json"

    first = await run_release_evaluation(manifest, random_seed=0)
    second = await run_release_evaluation(manifest, random_seed=0)

    assert first.gate_passed is True
    assert first.case_count >= 120
    assert first.workflow_execution_count >= 100
    assert first.unsafe_delivery_count == 0
    assert first.external_failure_misclassified_as_infeasible_count == 0
    assert first.provenance.dataset_sha256 == second.provenance.dataset_sha256
    assert first.provenance.config_fingerprint == second.provenance.config_fingerprint
    assert first.provenance.random_seed == 0

    def deterministic_projection(report):
        return [
            (
                case.case_id,
                case.run_status,
                case.terminal_reason,
                case.result_fingerprint,
                case.graph_steps,
                case.tool_calls,
                case.provider_attempts,
                case.cache_hits,
                case.llm_calls,
            )
            for case in report.cases
        ]

    assert deterministic_projection(first) == deterministic_projection(second)


@pytest.mark.asyncio
async def test_ablation_runner_executes_isolated_workflows_and_comparison_gates():
    manifest = ROOT / "evals" / "v1_0" / "manifest.json"

    report = await run_ablation_evaluation(manifest, random_seed=0)

    assert report.gate_passed is True
    assert report.workflow_execution_count == 180
    summaries = {item.variant: item for item in report.variants}
    assert summaries[EvaluationVariant.FULL].unsafe_delivery_count == 0
    assert summaries[EvaluationVariant.NO_VALIDATOR].unsafe_delivery_count > 0
    assert (
        summaries[EvaluationVariant.FULL].total_provider_attempts
        < summaries[EvaluationVariant.CACHE_OFF].total_provider_attempts
    )
    assert summaries[EvaluationVariant.NO_SOFT_CRITIC].total_llm_calls < summaries[
        EvaluationVariant.FULL
    ].total_llm_calls
