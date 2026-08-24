from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field


class MemoryScenario(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario_id: str
    category: str
    stored_value: str | list[str] | int
    current_value: str | list[str] | int | None = None
    confirmed: bool = True
    relevant: bool = True
    expected_selected: bool


class AblationMetrics(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    variant: str
    scenario_count: int = Field(ge=1)
    preference_hit_rate: float = Field(ge=0, le=1)
    wrong_personalization_rate: float = Field(ge=0, le=1)
    explicit_override_accuracy: float = Field(ge=0, le=1)
    context_characters: int = Field(ge=0)


class MemoryAblationReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["memory-ablation-report-v1"] = "memory-ablation-report-v1"
    dataset_version: str
    scenarios: int
    variants: tuple[AblationMetrics, ...]
    specialist_default_recommendation: Literal[
        "single_graph", "specialist_subagents", "insufficient_evidence"
    ] = "insufficient_evidence"


def load_memory_scenarios(path: str | Path) -> tuple[MemoryScenario, ...]:
    values = []
    for line_number, line in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            values.append(MemoryScenario.model_validate_json(line))
        except Exception as error:
            raise ValueError(f"invalid memory scenario at line {line_number}") from error
    if not values:
        raise ValueError("memory scenario dataset is empty")
    return tuple(values)


def run_memory_ablations(
    scenarios: Iterable[MemoryScenario], *, dataset_version: str = "v1.1-60"
) -> MemoryAblationReport:
    values = tuple(scenarios)
    variants = (
        _evaluate(values, "without_memory", use_memory=False, confirmed_only=True, bounded=True),
        _evaluate(values, "with_memory", use_memory=True, confirmed_only=True, bounded=True),
        _evaluate(values, "full_history", use_memory=True, confirmed_only=False, bounded=False),
        _evaluate(values, "confirmed_only", use_memory=True, confirmed_only=True, bounded=True),
        _evaluate(values, "inferred_memory", use_memory=True, confirmed_only=False, bounded=True),
    )
    return MemoryAblationReport(
        dataset_version=dataset_version,
        scenarios=len(values),
        variants=variants,
    )


def _evaluate(
    scenarios: tuple[MemoryScenario, ...],
    variant: str,
    *,
    use_memory: bool,
    confirmed_only: bool,
    bounded: bool,
) -> AblationMetrics:
    hits = wrong = overrides = override_total = context_characters = 0
    for scenario in scenarios:
        eligible = (
            use_memory
            and scenario.relevant
            and (scenario.confirmed or not confirmed_only)
            and scenario.current_value is None
        )
        selected = eligible
        if selected and scenario.expected_selected:
            hits += 1
        if selected and not scenario.expected_selected:
            wrong += 1
        if scenario.current_value is not None:
            override_total += 1
            if not selected:
                overrides += 1
        if selected:
            encoded = json.dumps(
                {"category": scenario.category, "value": scenario.stored_value},
                ensure_ascii=False,
            )
            context_characters += min(len(encoded), 80) if bounded else len(encoded)
    expected = sum(item.expected_selected for item in scenarios) or 1
    return AblationMetrics(
        variant=variant,
        scenario_count=len(scenarios),
        preference_hit_rate=hits / expected,
        wrong_personalization_rate=wrong / len(scenarios),
        explicit_override_accuracy=(overrides / override_total if override_total else 1.0),
        context_characters=context_characters,
    )
