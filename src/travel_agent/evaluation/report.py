from __future__ import annotations

from travel_agent.evaluation.models import ReleaseEvalReport


def report_as_markdown(report: ReleaseEvalReport) -> str:
    rows = "\n".join(
        f"| {gate.name} | {'PASS' if gate.passed else 'FAIL'} | {gate.actual} | {gate.expected} |"
        for gate in report.gates
    )
    return f"""# v1.0 Release Evaluation

- Dataset: `{report.dataset_version}`
- Project: `{report.project_version}`
- Profile: `{report.provider_profile}`
- Cases: {report.case_count}
- Workflow executions: {report.workflow_execution_count}
- Gate: {'PASS' if report.gate_passed else 'FAIL'}
- Reproducibility: `{report.provenance.reproducibility_fingerprint}`
- Git: `{report.provenance.git_commit or 'unknown'}` ({'dirty' if report.provenance.git_dirty else 'clean/unknown'})
- Pricing: `{report.provenance.pricing_registry_version or 'not_applicable'}`

| Gate | Result | Actual | Expected |
|---|---:|---:|---:|
{rows}

## Usage

- Graph steps: {report.total_graph_steps}
- Tool calls: {report.total_tool_calls}
- Provider attempts: {report.total_provider_attempts}
- Cache hits: {report.total_cache_hits}
- LLM calls: {report.total_llm_calls}
- Input tokens: {report.total_input_tokens if report.total_input_tokens is not None else 'unknown'}
- Output tokens: {report.total_output_tokens if report.total_output_tokens is not None else 'unknown'}
- Estimated cost (microunits): {report.total_estimated_cost_microunits if report.total_estimated_cost_microunits is not None else 'unknown/not_applicable'}
- Unsafe deliveries: {report.unsafe_delivery_count}
- External failures misclassified as infeasible: {report.external_failure_misclassified_as_infeasible_count}
"""
