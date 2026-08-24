from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class EvidenceLevel(StrEnum):
    WORKFLOW_EXECUTION = "workflow_execution"
    COMPONENT_EXECUTION = "component_execution"
    ANNOTATED_CONTRACT = "annotated_contract"
    LIVE_PROVIDER = "live_provider"


class EvaluationVariant(StrEnum):
    FULL = "full"
    CACHE_REPEAT = "cache_repeat"
    BUDGET_FAULT = "budget_fault"
    LLM_FAULT = "llm_fault"
    DIRECT_LLM = "direct_llm"
    NO_VALIDATOR = "no_validator"
    NO_OPTIMIZER = "no_optimizer"
    NO_SOFT_CRITIC = "no_soft_critic"
    FULL_REPLAN = "full_replan"
    CACHE_OFF = "cache_off"


class EvaluationMatrixEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    variant: EvaluationVariant
    evidence_level: EvidenceLevel = EvidenceLevel.WORKFLOW_EXECUTION
    expected_terminal: str


class ReleaseManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str
    dataset_version: str
    base_dataset: str
    minimum_case_count: int = Field(ge=1)
    minimum_workflow_execution_count: int = Field(ge=1)
    matrix: tuple[EvaluationMatrixEntry, ...]


class ReleaseCaseResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    source_case_id: str
    category: str
    variant: EvaluationVariant
    evidence_level: EvidenceLevel
    run_id: str
    run_status: str
    terminal_reason: str
    expected_behavior_met: bool
    hard_constraints_satisfied: bool | None
    trace_complete: bool
    bounded: bool
    graph_steps: int
    tool_calls: int
    provider_attempts: int
    cache_hits: int
    llm_calls: int
    input_tokens: int | None
    output_tokens: int | None
    elapsed_ms: int
    result_fingerprint: str | None = None


class GateResult(BaseModel):
    name: str
    passed: bool
    actual: int | float | str
    expected: int | float | str


class ReportProvenance(BaseModel):
    """发布报告的可复现来源；字段只包含公开配置和内容指纹。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    git_commit: str | None
    git_dirty: bool | None
    python_version: str
    platform: str
    runner_schema_version: str = "v1-release-runner-v1"
    trace_schema_version: str = "trace-event-v1"
    manifest_sha256: str
    dataset_sha256: str
    config_fingerprint: str
    fixture_fingerprint: str
    random_seed: int
    provider_models: tuple[str, ...]
    prompt_versions: tuple[str, ...]
    usage_kind: str
    pricing_registry_version: str | None
    reproducibility_fingerprint: str


class ReleaseEvalReport(BaseModel):
    schema_version: str = "v1-release-report-v1"
    dataset_version: str
    project_version: str
    provider_profile: str
    started_at: datetime
    ended_at: datetime
    case_count: int
    workflow_execution_count: int
    completed_count: int
    interrupted_count: int
    failed_count: int
    completed_hard_constraint_satisfaction_rate: float
    bounded_termination_rate: float
    failure_classification_accuracy: float
    trace_completeness_rate: float
    total_graph_steps: int
    total_tool_calls: int
    total_provider_attempts: int
    total_cache_hits: int
    total_llm_calls: int
    total_input_tokens: int | None
    total_output_tokens: int | None
    total_estimated_cost_microunits: int | None = None
    unsafe_delivery_count: int = 0
    external_failure_misclassified_as_infeasible_count: int = 0
    provenance: ReportProvenance
    cases: tuple[ReleaseCaseResult, ...]
    gates: tuple[GateResult, ...]
    gate_passed: bool


class VariantSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    variant: EvaluationVariant
    case_count: int
    completed_plan_count: int
    hard_constraint_satisfaction_rate: float
    unsafe_delivery_count: int
    trace_completeness_rate: float
    bounded_termination_rate: float
    total_tool_calls: int
    total_provider_attempts: int
    total_cache_hits: int
    total_llm_calls: int
    latency_p50_ms: int
    latency_p95_ms: int


class AblationEvalReport(BaseModel):
    schema_version: str = "v1-ablation-report-v1"
    dataset_version: str
    project_version: str
    started_at: datetime
    ended_at: datetime
    case_count: int
    workflow_execution_count: int
    variants: tuple[VariantSummary, ...]
    cases: tuple[ReleaseCaseResult, ...]
    comparison_gates: tuple[GateResult, ...]
    gate_passed: bool
    provenance: ReportProvenance
